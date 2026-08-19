"""
Read-only inspection of the smriti corpus.

This is the seam between smriti and the Chief of Staff page that renders the
inspector. CoS is a separate repository; if its page queried `code_kosha`
directly, smriti's schema knowledge would live somewhere that does not move when
the schema moves — yesterday's `semantic_unit` split would have broken it
silently, still reading `symbol_name` and presenting chunker regions as parsed
symbols. So schema knowledge stays here and the page is a presentation-only
client of these endpoints.

It also keeps the repository clones out of CoS: they are already bind-mounted
into this container at /repos for CodeFetcher, so source can be served without
mounting smriti's data anywhere else.

Nothing here writes. Judgments are a separate surface with different
consequences, and mixing them into a read API would make it hard to reason about
which calls can change the corpus.
"""

import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Annotated, Dict, List, Optional

import httpx
from couchbase.options import QueryOptions
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, Field

from ..database.couchbase_client import CouchbaseClient
from ..dependencies import get_current_user, get_db

router = APIRouter()

# Types the parser emits, as opposed to the LLM chunker's `semantic_unit`.
CHILD_TYPES = ("symbol_index", "semantic_unit")

# A file bigger than this is not something anyone is reading to judge a summary,
# and shipping it would just stall the browser.
MAX_SOURCE_BYTES = 2_000_000

# Couchbase identifiers cannot be parameterised, so the bucket name is
# interpolated. It arrives from a signed JWT claim, but a signed claim is still
# input: anything that reaches a query string gets checked first.
_SAFE_IDENT = re.compile(r"^[A-Za-z0-9_-]+$")


def _bucket(current_user: dict) -> str:
    name = current_user.get("tenant_id") or "code_kosha"
    if not _SAFE_IDENT.match(name):
        raise HTTPException(status_code=400, detail="Invalid tenant identifier")
    return name


def _repos_root() -> Path:
    return Path(os.getenv("REPOS_PATH", "/repos"))


def _repo_root(repo_id: str) -> Path:
    """Clones are stored with the slash in repo_id flattened to an underscore."""
    return _repos_root() / repo_id.replace("/", "_")


def _resolve(repo_id: str, relative: str) -> Path:
    """
    Resolve a client-supplied path inside one repository, or refuse.

    `relative` comes from the URL, so it is checked rather than trusted: a path
    that escapes the repository root after resolution is rejected outright
    instead of being clamped, because there is no legitimate request that lands
    outside the clone.
    """
    root = _repo_root(repo_id).resolve()
    target = (root / relative).resolve()
    if target != root and not target.is_relative_to(root):
        raise HTTPException(status_code=400, detail="Path escapes repository root")
    return target


def _snippet_digest(content: str, start_line: int, end_line: int) -> str:
    """
    Hash the span exactly the way ingestion did, or staleness is meaningless.

    Mirrors `FileProcessor.get_code_snippet` followed by `make_content_hash`;
    the two must agree line-for-line, so this deliberately repeats their
    arithmetic rather than approximating it.
    """
    lines = content.split("\n")
    snippet = "\n".join(lines[max(0, start_line - 1):min(len(lines), end_line)])
    return hashlib.sha256(snippet.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class RepoRow(BaseModel):
    repo_id: str
    modules: int = 0
    files: int = 0
    symbols: int = 0
    has_summary: bool = False


class ReposResponse(BaseModel):
    repos: List[RepoRow]
    total_repos: int


class TreeFile(BaseModel):
    path: str
    document_id: str
    symbols: int = 0
    semantic_units: int = 0
    language: Optional[str] = None


class TreeModule(BaseModel):
    path: str
    document_id: str


class TreeResponse(BaseModel):
    repo_id: str
    modules: List[TreeModule]
    files: List[TreeFile]
    orphan_files: List[str] = Field(
        default_factory=list,
        description="Indexed files whose module has no module_summary document",
    )


class DirEntry(BaseModel):
    name: str
    kind: str  # "dir" | "file"
    indexed: bool
    indexed_as: Optional[str] = Field(
        default=None,
        description="file_index (code), document (prose chunks), or spec",
    )


class DirResponse(BaseModel):
    repo_id: str
    path: str
    entries: List[DirEntry]


class ChildDoc(BaseModel):
    document_id: str
    kind: str  # "symbol" | "semantic_unit"
    name: str
    type: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    summary: str = ""
    summary_source: Optional[str] = None
    enrichment_level: Optional[str] = None
    staleness: str = "unknown"  # "fresh" | "stale" | "unknown"


class FileResponse(BaseModel):
    repo_id: str
    path: str
    document_id: Optional[str] = None
    summary: str = ""
    language: Optional[str] = None
    source: Optional[str] = None
    line_count: int = 0
    truncated: bool = False
    children: List[ChildDoc] = Field(default_factory=list)


class ChainLevel(BaseModel):
    level: str  # symbol | semantic_unit | file | module | repo
    document_id: str
    name: str
    summary: str = ""
    summary_source: Optional[str] = None


class ChainResponse(BaseModel):
    levels: List[ChainLevel]
    broken_at: Optional[str] = Field(
        default=None,
        description="document_id of a parent_id that resolves to nothing",
    )


class Neighbor(BaseModel):
    document_id: str
    repo_id: str
    file_path: str
    name: str
    type: Optional[str] = None
    start_line: int = 0
    end_line: int = 0
    summary: str = ""
    similarity: float


class NeighborsResponse(BaseModel):
    document_id: str
    neighbors: List[Neighbor]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/repos", response_model=ReposResponse)
async def list_repos(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[CouchbaseClient, Depends(get_db)],
):
    """
    Every indexed repository, with what smriti holds for it.

    `semantic_unit` is deliberately not counted here. It is absent from
    `idx_repo_doc_stats`'s WHERE clause, so a corpus-wide count of it falls back
    to scanning `adv_repo_id` and fetching documents to filter — slow enough to
    matter on a page that loads first. Those counts appear in the tree, where the
    query is scoped to one repository.
    """
    bucket = _bucket(current_user)
    rows = await db.query(f"""
        SELECT d.repo_id, d.type, COUNT(*) AS n
        FROM `{bucket}` d
        WHERE d.type IN ['file_index', 'symbol_index', 'module_summary', 'repo_summary']
        GROUP BY d.repo_id, d.type
    """)

    repos: Dict[str, RepoRow] = {}
    for row in rows:
        repo_id = row.get("repo_id")
        if not repo_id:
            continue
        entry = repos.setdefault(repo_id, RepoRow(repo_id=repo_id))
        count, doc_type = row["n"], row["type"]
        if doc_type == "file_index":
            entry.files = count
        elif doc_type == "symbol_index":
            entry.symbols = count
        elif doc_type == "module_summary":
            entry.modules = count
        elif doc_type == "repo_summary":
            entry.has_summary = True

    ordered = sorted(repos.values(), key=lambda r: r.repo_id)
    return ReposResponse(repos=ordered, total_repos=len(ordered))


@router.get("/tree", response_model=TreeResponse)
async def repo_tree(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[CouchbaseClient, Depends(get_db)],
    repo_id: str = Query(..., description="owner/name"),
):
    """
    One repository's document hierarchy — what smriti believes it holds.

    This is the document tree, not the filesystem tree, and the difference is the
    point: a file present on disk but never indexed cannot appear here, which is
    exactly why /dir exists to check one level against reality.

    Fetched per repository rather than per file. A query per file is thousands of
    round trips and was 99% of the runtime of the corpus sweep that prompted this
    UI; scoping to a repo turned that from 33 minutes into 2.
    """
    bucket = _bucket(current_user)
    opts = QueryOptions(named_parameters={"r": repo_id})

    docs, child_counts = await asyncio.gather(
        db.query(f"""
            SELECT d.type, d.document_id, d.file_path, d.module_path,
                   d.metadata.`language` AS lang
            FROM `{bucket}` d
            WHERE d.repo_id = $r AND d.type IN ['file_index', 'module_summary']
        """, opts),
        db.query(f"""
            SELECT d.file_path, d.type, COUNT(*) AS n
            FROM `{bucket}` d
            WHERE d.repo_id = $r AND d.type IN ['symbol_index', 'semantic_unit']
            GROUP BY d.file_path, d.type
        """, opts),
    )

    counts: Dict[str, Dict[str, int]] = {}
    for row in child_counts:
        path = row.get("file_path")
        if path:
            counts.setdefault(path, {})[row["type"]] = row["n"]

    modules: List[TreeModule] = []
    files: List[TreeFile] = []
    for doc in docs:
        if doc["type"] == "module_summary":
            modules.append(TreeModule(
                path=doc.get("module_path") or "",
                document_id=doc["document_id"],
            ))
        else:
            path = doc.get("file_path") or ""
            per_type = counts.get(path, {})
            files.append(TreeFile(
                path=path,
                document_id=doc["document_id"],
                symbols=per_type.get("symbol_index", 0),
                semantic_units=per_type.get("semantic_unit", 0),
                language=doc.get("lang"),
            ))

    # A file whose directory has no module_summary is a real gap, not a display
    # quirk: the rollup that should summarise it does not exist. Surfaced rather
    # than smoothed over, because it is one of the defects still outstanding.
    module_paths = {m.path for m in modules}
    orphans = [
        f.path for f in files
        if str(Path(f.path).parent) not in module_paths
        and not (str(Path(f.path).parent) == "." and "" in module_paths)
    ]

    return TreeResponse(
        repo_id=repo_id,
        modules=sorted(modules, key=lambda m: m.path),
        files=sorted(files, key=lambda f: f.path),
        orphan_files=sorted(orphans),
    )


@router.get("/dir", response_model=DirResponse)
async def list_directory(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[CouchbaseClient, Depends(get_db)],
    repo_id: str = Query(..., description="owner/name"),
    path: str = Query("", description="Directory relative to the repo root"),
):
    """
    One directory on disk, marking which entries smriti actually indexed.

    Deliberately one level and never recursive. A repository-wide walk means
    descending into node_modules and .git for a result nobody asked for; the UI
    reveals a level at a time, so this answers a level at a time.

    A file listed here with indexed=false is present on disk with no `file_index`
    document — the single most useful thing this endpoint reports, and something
    neither the document tree nor a plain file listing can show alone.
    """
    bucket = _bucket(current_user)
    target = _resolve(repo_id, path)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    # Every type that claims a file_path, not just file_index. Markdown and RST
    # are indexed as chunked `document`s and feature specs as `spec`s, so
    # checking file_index alone reports README.md as a gap — and a gap detector
    # with false positives is one nobody reads.
    indexed: Dict[str, str] = {}
    for row in await db.query(
        f"""
        SELECT DISTINCT d.file_path, d.type FROM `{bucket}` d
        WHERE d.repo_id = $r AND d.type IN ['file_index', 'document', 'spec']
        """,
        QueryOptions(named_parameters={"r": repo_id}),
    ):
        path_value = row.get("file_path")
        if path_value:
            indexed.setdefault(path_value, row["type"])

    prefix = f"{path.rstrip('/')}/" if path.strip("/") else ""
    entries: List[DirEntry] = []
    for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
        if child.name.startswith("."):
            continue
        rel = f"{prefix}{child.name}"
        if child.is_dir():
            entries.append(DirEntry(name=child.name, kind="dir", indexed=True))
        else:
            indexed_as = indexed.get(rel)
            entries.append(DirEntry(
                name=child.name, kind="file",
                indexed=indexed_as is not None, indexed_as=indexed_as,
            ))

    return DirResponse(repo_id=repo_id, path=path, entries=entries)


@router.get("/file", response_model=FileResponse)
async def inspect_file(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[CouchbaseClient, Depends(get_db)],
    repo_id: str = Query(..., description="owner/name"),
    path: str = Query(..., description="File relative to the repo root"),
    include_source: bool = Query(True),
):
    """
    A file's own document, its children, and the source they claim to describe.

    Source is read from disk because smriti stores none — only summaries and
    spans. That is what makes the centre pane a check rather than a display: each
    span is rendered against current disk content, so a highlight landing on the
    wrong function is visible instead of inferred.

    `staleness` compares each child's stored `content_hash` against a hash of the
    span as it reads today. Documents written before that field existed report
    "unknown" rather than "stale" — absence of a hash is not evidence of drift,
    and reporting it as such would paint most of the corpus red.
    """
    bucket = _bucket(current_user)
    opts = QueryOptions(named_parameters={"r": repo_id, "f": path})

    file_docs, child_docs = await asyncio.gather(
        db.query(f"""
            SELECT d.document_id, d.content, d.metadata.`language` AS lang
            FROM `{bucket}` d
            WHERE d.repo_id = $r AND d.file_path = $f AND d.type = 'file_index'
        """, opts),
        db.query(f"""
            SELECT d.document_id, d.type, d.symbol_name, d.symbol_type,
                   d.label, d.unit_type, d.content,
                   d.metadata.start_line AS start_line,
                   d.metadata.end_line AS end_line,
                   d.metadata.content_hash AS content_hash,
                   d.quality.summary_source AS summary_source,
                   d.quality.enrichment_level AS enrichment_level
            FROM `{bucket}` d
            WHERE d.repo_id = $r AND d.file_path = $f
              AND d.type IN ['symbol_index', 'semantic_unit']
        """, opts),
    )

    source: Optional[str] = None
    truncated = False
    line_count = 0
    if include_source:
        disk = _resolve(repo_id, path)
        if disk.is_file():
            raw = disk.read_bytes()
            truncated = len(raw) > MAX_SOURCE_BYTES
            source = raw[:MAX_SOURCE_BYTES].decode("utf-8", errors="replace")
            line_count = source.count("\n") + 1

    children: List[ChildDoc] = []
    for doc in child_docs:
        is_symbol = doc["type"] == "symbol_index"
        start, end = doc.get("start_line") or 0, doc.get("end_line") or 0
        stored = doc.get("content_hash")
        if not stored or source is None or not start or not end:
            staleness = "unknown"
        else:
            staleness = "fresh" if stored == _snippet_digest(source, start, end) else "stale"
        children.append(ChildDoc(
            document_id=doc["document_id"],
            kind="symbol" if is_symbol else "semantic_unit",
            name=(doc.get("symbol_name") if is_symbol else doc.get("label")) or "",
            type=(doc.get("symbol_type") if is_symbol else doc.get("unit_type")),
            start_line=start,
            end_line=end,
            summary=doc.get("content") or "",
            summary_source=doc.get("summary_source"),
            enrichment_level=doc.get("enrichment_level"),
            staleness=staleness,
        ))
    children.sort(key=lambda c: (c.start_line, c.end_line))

    head = file_docs[0] if file_docs else {}
    return FileResponse(
        repo_id=repo_id,
        path=path,
        document_id=head.get("document_id"),
        summary=head.get("content") or "",
        language=head.get("lang"),
        source=source,
        line_count=line_count,
        truncated=truncated,
        children=children,
    )


@router.get("/chain", response_model=ChainResponse)
async def ancestry_chain(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[CouchbaseClient, Depends(get_db)],
    document_id: str = Query(...),
):
    """
    A document and every summary above it, innermost first.

    The premise of the v4 hierarchy is that summaries roll up, and the defects
    still outstanding are rollup defects — module summaries that do not reflect
    their files. Seeing the levels together is how that becomes visible, so they
    are returned as one stack rather than fetched a level at a time.

    A `parent_id` pointing at nothing is reported in `broken_at` rather than
    ending the walk quietly: roughly 472 file→module links currently dangle, and
    a chain that simply stopped short would hide them.
    """
    bucket = _bucket(current_user)
    levels: List[ChainLevel] = []
    broken_at: Optional[str] = None

    LEVEL_OF = {
        "symbol_index": "symbol",
        "semantic_unit": "semantic_unit",
        "file_index": "file",
        "module_summary": "module",
        "repo_summary": "repo",
    }

    current_id: Optional[str] = document_id
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        doc = await db.get_doc(bucket, current_id)
        if doc is None:
            broken_at = current_id
            break
        levels.append(ChainLevel(
            level=LEVEL_OF.get(doc.get("type", ""), doc.get("type", "unknown")),
            document_id=current_id,
            name=(
                doc.get("symbol_name") or doc.get("label")
                or doc.get("file_path") or doc.get("module_path")
                or doc.get("repo_id") or ""
            ),
            summary=doc.get("content") or "",
            summary_source=(doc.get("quality") or {}).get("summary_source"),
        ))
        current_id = doc.get("parent_id")

    if not levels:
        raise HTTPException(status_code=404, detail="Document not found")
    return ChainResponse(levels=levels, broken_at=broken_at)


@router.get("/neighbors", response_model=NeighborsResponse)
async def nearest_neighbors(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: Annotated[CouchbaseClient, Depends(get_db)],
    document_id: str = Query(...),
    k: int = Query(10, ge=1, le=50),
):
    """
    The k nearest documents to this one, by its own stored vector.

    No embedding model is involved: the document already holds a unit-norm
    768-float vector, so this is a lookup and a search rather than an inference.

    Two things are inherited from `rag.tools`, both learned the hard way against
    this index. One KNN query per type, never a `disjuncts` filter — combined
    with knn_operator "and" a disjunction silently returns hits from only one of
    its clauses. And ranking is recomputed here rather than taken from FTS: in
    query+knn mode the scores arrive flat and whole result sets tie exactly, so
    the returned order is arbitrary. Vectors are unit-norm, so a dot product is
    the cosine.
    """
    bucket = _bucket(current_user)
    doc = await db.get_doc(bucket, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    vector = doc.get("embedding")
    if not vector:
        raise HTTPException(status_code=409, detail="Document has no embedding")

    doc_type = doc.get("type", "symbol_index")
    host = os.getenv("COUCHBASE_HOST", "localhost")
    user = os.getenv("COUCHBASE_USERNAME", "Administrator")
    password = os.environ["COUCHBASE_PASSWORD"]
    fts_url = f"http://{host}:8094/api/index/code_vector_index/query"

    # Oversampled because the candidate set is re-ranked below and the document
    # itself is dropped from it. Kept <= 100: on 7.6.2 larger k breaks the type
    # filter outright.
    oversample = min(max(k * 5, 20), 100)
    request = {
        "query": {"term": doc_type, "field": "type"},
        "knn": [{"field": "embedding", "vector": vector, "k": oversample}],
        "knn_operator": "and",
        "size": oversample,
        "fields": ["type"],
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(fts_url, json=request, auth=(user, password), timeout=30.0)
    if resp.status_code != 200:
        logger.error(f"FTS neighbour search failed: {resp.status_code} - {resp.text}")
        raise HTTPException(status_code=502, detail="Vector search unavailable")

    hit_ids = [h["id"] for h in resp.json().get("hits", []) if h.get("id") != document_id]
    if not hit_ids:
        return NeighborsResponse(document_id=document_id, neighbors=[])

    rows = await db.query(
        f"""
        SELECT d.document_id, d.repo_id, d.file_path, d.symbol_name, d.label,
               d.symbol_type, d.unit_type, d.content, d.embedding,
               d.metadata.start_line AS start_line, d.metadata.end_line AS end_line
        FROM `{bucket}` d USE KEYS $ids
        """,
        QueryOptions(named_parameters={"ids": hit_ids}),
    )

    scored: List[Neighbor] = []
    for row in rows:
        other = row.get("embedding")
        if not other or len(other) != len(vector):
            continue
        scored.append(Neighbor(
            document_id=row["document_id"],
            repo_id=row.get("repo_id") or "",
            file_path=row.get("file_path") or "",
            name=row.get("symbol_name") or row.get("label") or "",
            type=row.get("symbol_type") or row.get("unit_type"),
            start_line=row.get("start_line") or 0,
            end_line=row.get("end_line") or 0,
            summary=row.get("content") or "",
            similarity=sum(a * b for a, b in zip(vector, other)),
        ))

    scored.sort(key=lambda n: n.similarity, reverse=True)
    return NeighborsResponse(document_id=document_id, neighbors=scored[:k])
