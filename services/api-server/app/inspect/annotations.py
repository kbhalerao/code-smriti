"""
Judgments on smriti documents — the write half of the inspector.

A judgment records whether a generated summary is any good. Presence is measured
exhaustively across this corpus and quality has never been measured at all, so
these are the first labelled data about it: an evaluation set built as a
byproduct of reading, which is what makes a model or prompt change something you
can measure rather than something you can only feel.

Kept out of Chief of Staff deliberately. Judgments are per-symbol and
high-volume; issues are few and need follow-up. Conflating them puts twelve
thousand notes in an inbox. An issue raised from a judgment is created through
CoS's own API — that service owns its schema and credentials, and this one does
not hand-roll its documents — and links back to the annotation by id, so
"which judgments became issues" is a question CoS answers.

Append-only. You judge, fix, and re-judge, and the delta is the measurement, so
nothing here updates or deletes: the current verdict is simply the newest per
(target, author).
"""

import hashlib
from datetime import datetime
from typing import Dict, List, Literal, Optional

from couchbase.n1ql import QueryScanConsistency
from couchbase.options import QueryOptions
from fastapi import HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from ..database.couchbase_client import CouchbaseClient

# Judgments are read straight back after being written — the UI shows the verdict
# it just recorded. The default scan consistency is not_bounded, under which a
# document that was upserted a moment ago may not be in the index yet, so a fresh
# judgment silently vanishes from the list. Annotations are a small index and
# these are interactive reads, so the wait is cheap and the alternative is a UI
# that appears to lose data.
def consistent(params: Optional[Dict] = None) -> QueryOptions:
    if params:
        return QueryOptions(
            named_parameters=params, scan_consistency=QueryScanConsistency.REQUEST_PLUS
        )
    return QueryOptions(scan_consistency=QueryScanConsistency.REQUEST_PLUS)


Verdict = Literal["good", "generic", "inaccurate", "misattributed"]

# Types that carry a generated summary and can therefore be judged.
JUDGEABLE = ("symbol_index", "semantic_unit", "file_index", "module_summary", "repo_summary")


class AnnotationCreate(BaseModel):
    """
    What a client may send.

    Only the target and the judgment. Everything describing the *subject* is read
    from the document server-side — a client-supplied snapshot would let a stale
    browser tab record a judgment against text that had already been replaced.
    """
    target_id: str
    verdict: Verdict
    note: str = ""


class Annotation(BaseModel):
    document_id: str
    target_id: str
    target_type: str
    verdict: Verdict
    note: str = ""
    author: str
    author_id: str
    created_at: str
    anchor: Dict = Field(default_factory=dict)
    judged: Dict = Field(default_factory=dict)
    status: str = "current"  # current | content_changed | target_missing


class VerdictStats(BaseModel):
    total: int = 0
    by_verdict: Dict[str, int] = Field(default_factory=dict)
    by_summary_source: Dict[str, Dict[str, int]] = Field(default_factory=dict)
    documents_judged: int = 0


def make_annotation_id(target_id: str, author_id: str, created_at: str) -> str:
    """
    Identity for one judgment.

    Includes the timestamp because judgments accumulate rather than replace: the
    same person judging the same document after a fix is a second data point, not
    a correction of the first.
    """
    return hashlib.sha256(f"annotation:{target_id}:{author_id}:{created_at}".encode()).hexdigest()


def _anchor_from(doc: Dict) -> Dict:
    """
    Enough context to find this symbol again if its id stops resolving.

    Ids are stable for a named symbol across re-ingestion and across the symbol
    moving down its file, but a rename mints a new id, and symbols the parser
    could not name are keyed by span so moving one re-keys it. Those annotations
    would otherwise dangle with nothing to re-attach them by.
    """
    metadata = doc.get("metadata") or {}
    return {
        "repo_id": doc.get("repo_id") or "",
        "file_path": doc.get("file_path") or "",
        "symbol_name": doc.get("symbol_name") or doc.get("label") or "",
        "start_line": metadata.get("start_line") or 0,
        "end_line": metadata.get("end_line") or 0,
        "content_hash": metadata.get("content_hash") or "",
    }


def _judged_from(doc: Dict) -> Dict:
    """
    A copy of what was actually judged.

    Stable identity made the anchor durable and the subject mutable: documents
    are now upserted in place, so the next ingest overwrites the very text this
    verdict is about. Provenance is copied for the same reason — a summary judged
    good under one model is not evidence about another, and an evaluation set
    that silently mixes generations measures nothing.
    """
    quality = doc.get("quality") or {}
    version = doc.get("version") or {}
    return {
        "content": doc.get("content") or "",
        "summary_source": quality.get("summary_source") or "",
        "enrichment_level": quality.get("enrichment_level") or "",
        "pipeline_version": version.get("pipeline_version") or "",
        "schema_version": version.get("schema_version") or "",
    }


async def create_annotation(
    db: CouchbaseClient,
    bucket: str,
    payload: AnnotationCreate,
    author: str,
    author_id: str,
) -> Annotation:
    target = await db.get_doc(bucket, payload.target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Target document not found")
    if target.get("type") not in JUDGEABLE:
        raise HTTPException(
            status_code=422,
            detail=f"Documents of type {target.get('type')!r} carry no generated summary",
        )

    created_at = datetime.now().isoformat()
    doc_id = make_annotation_id(payload.target_id, author_id, created_at)
    record = {
        "type": "annotation",
        "document_id": doc_id,
        "target_id": payload.target_id,
        "target_type": target["type"],
        "verdict": payload.verdict,
        "note": payload.note,
        "author": author,
        "author_id": author_id,
        "created_at": created_at,
        "anchor": _anchor_from(target),
        "judged": _judged_from(target),
    }

    def _write() -> None:
        db.cluster.bucket(bucket).default_collection().upsert(doc_id, record)

    from starlette.concurrency import run_in_threadpool
    await run_in_threadpool(_write)
    logger.info(f"annotation {payload.verdict} on {payload.target_id} by {author}")
    return Annotation(**record, status="current")


def _status_of(record: Dict, target: Optional[Dict]) -> str:
    """
    Whether this judgment still describes the document it was made about.

    Reported rather than repaired. A changed hash means the code moved on and the
    verdict needs revisiting; guessing which new symbol it now refers to would
    put invented data into the one dataset that exists to be trustworthy.
    """
    if target is None:
        return "target_missing"
    stored = (record.get("anchor") or {}).get("content_hash")
    current = (target.get("metadata") or {}).get("content_hash")
    if not stored or not current:
        # Documents written before content_hash existed cannot be compared.
        # Saying "current" would claim a check that never happened.
        return "unknown"
    return "current" if stored == current else "content_changed"


async def annotations_for_target(
    db: CouchbaseClient, bucket: str, target_id: str
) -> List[Annotation]:
    rows = await db.query(
        f"""
        SELECT d.* FROM `{bucket}` d
        WHERE d.type = 'annotation' AND d.target_id = $t
        ORDER BY d.created_at DESC
        """,
        consistent({"t": target_id}),
    )
    target = await db.get_doc(bucket, target_id)
    return [Annotation(**r, status=_status_of(r, target)) for r in rows]


async def annotations_for_repo(
    db: CouchbaseClient,
    bucket: str,
    repo_id: str,
    limit: int,
    file_path: Optional[str] = None,
) -> List[Annotation]:
    """
    Judgments across a repository, or within one file.

    The file filter exists for the gutter: colouring every document's rail by its
    verdict needs the whole file's judgments in one call, and asking per document
    would be one request per symbol on every file open.
    """
    where = "d.type = 'annotation' AND d.anchor.repo_id = $r"
    params: Dict[str, object] = {"r": repo_id, "lim": limit}
    if file_path is not None:
        where += " AND d.anchor.file_path = $f"
        params["f"] = file_path
    rows = await db.query(
        f"""
        SELECT d.* FROM `{bucket}` d
        WHERE {where}
        ORDER BY d.created_at DESC
        LIMIT $lim
        """,
        consistent(params),
    )
    return [Annotation(**r) for r in rows]


async def verdict_stats(
    db: CouchbaseClient, bucket: str, repo_id: Optional[str]
) -> VerdictStats:
    """
    The evaluation set as it currently stands.

    Broken down by `summary_source` as well as verdict, because that is the whole
    point: "did the class backfill produce better summaries than the original
    pipeline" is a question about one column against another, not a single score.
    """
    where = "d.type = 'annotation'"
    params: Dict[str, object] = {}
    if repo_id:
        where += " AND d.anchor.repo_id = $r"
        params["r"] = repo_id

    rows = await db.query(
        f"""
        SELECT d.verdict, d.judged.summary_source AS source, COUNT(*) AS n
        FROM `{bucket}` d
        WHERE {where}
        GROUP BY d.verdict, d.judged.summary_source
        """,
        consistent(params),
    )
    # Counted separately, not summed out of the groups above: COUNT(DISTINCT)
    # inside a GROUP BY counts within each group, so a document judged twice
    # under different verdicts would be counted once per group.
    distinct = await db.query(
        f"""
        SELECT RAW COUNT(DISTINCT d.target_id) FROM `{bucket}` d WHERE {where}
        """,
        consistent(params),
    )

    stats = VerdictStats(documents_judged=distinct[0] if distinct else 0)
    for row in rows:
        verdict, source, count = row["verdict"], row.get("source") or "unknown", row["n"]
        stats.total += count
        stats.by_verdict[verdict] = stats.by_verdict.get(verdict, 0) + count
        stats.by_summary_source.setdefault(source, {})[verdict] = count
    return stats
