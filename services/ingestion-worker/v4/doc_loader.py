"""
Rehydrating v4 documents out of Couchbase.

The write path builds `FileIndex` / `ModuleSummary` dataclasses and serialises
them via `to_dict()`. Anything that reads them back — the incremental updater,
backfills, evals — has to reverse that, and every place doing it by hand has so
far dropped fields. `updater._regenerate_summaries` rebuilt FileIndex objects
without `metadata.symbols`, which silently starved the symbol-aware module
context of the only thing it consumes.

Child resolution here works off *paths*, not `children_ids`. Stored
`children_ids` can reference documents that no longer exist — module summaries
built before the superseded-doc purge point at file docs that purge removed — so
resolving structurally is both self-healing and independent of how stale the
parent doc is.
"""

from typing import Dict, List, Optional, Tuple

from .doc_versions import file_key, newest_per_key
from .schemas import FileIndex, ModuleSummary, SymbolRef

BUCKET = "code_kosha"


def rehydrate_symbols(metadata: Dict) -> List[SymbolRef]:
    """Rebuild SymbolRef list from `metadata.symbols`."""
    symbols = []
    for s in metadata.get("symbols") or []:
        lines = s.get("lines") or []
        symbols.append(SymbolRef(
            name=s.get("name", ""),
            symbol_type=s.get("type", ""),
            start_line=lines[0] if len(lines) > 0 else 0,
            end_line=lines[1] if len(lines) > 1 else 0,
            docstring=s.get("docstring"),
            methods=s.get("methods") or [],
        ))
    return symbols


def rehydrate_file_index(doc: Dict, commit_hash: Optional[str] = None) -> FileIndex:
    """
    Rebuild a FileIndex from its stored document.

    `commit_hash` overrides the stored value for callers restamping docs onto the
    commit currently being processed.
    """
    metadata = doc.get("metadata") or {}
    return FileIndex(
        document_id=doc.get("document_id") or "",
        repo_id=doc.get("repo_id") or "",
        file_path=doc.get("file_path") or "",
        commit_hash=commit_hash or doc.get("commit_hash") or "",
        content=doc.get("content") or "",
        line_count=metadata.get("line_count", doc.get("line_count", 0)),
        language=metadata.get("language", doc.get("language", "unknown")),
        imports=metadata.get("imports", doc.get("imports", [])) or [],
        symbols=rehydrate_symbols(metadata),
        parent_id=doc.get("parent_id") or "",
        children_ids=doc.get("children_ids") or [],
    )


def rehydrate_module_summary(doc: Dict) -> ModuleSummary:
    """Rebuild a ModuleSummary from its stored document."""
    metadata = doc.get("metadata") or {}
    return ModuleSummary(
        document_id=doc.get("document_id") or "",
        repo_id=doc.get("repo_id") or "",
        module_path=doc.get("module_path") or "",
        commit_hash=doc.get("commit_hash") or "",
        content=doc.get("content") or "",
        file_count=metadata.get("file_count", 0),
        key_files=metadata.get("key_files") or [],
        parent_id=doc.get("parent_id") or "",
        children_ids=doc.get("children_ids") or [],
    )


def is_direct_child_file(file_path: str, module_path: str) -> bool:
    """True when file_path sits directly in module_path, not in a subfolder."""
    if not module_path:
        return "/" not in file_path
    if not file_path.startswith(f"{module_path}/"):
        return False
    return "/" not in file_path[len(module_path) + 1:]


def is_direct_child_module(child_path: str, module_path: str) -> bool:
    """True when child_path is an immediate submodule of module_path."""
    if not module_path:
        return bool(child_path) and "/" not in child_path
    if not child_path.startswith(f"{module_path}/"):
        return False
    return "/" not in child_path[len(module_path) + 1:]


def module_key(doc: Dict) -> tuple:
    return (doc.get("repo_id") or "", doc.get("module_path") or "")


def load_repo_file_docs(cluster, repo_id: str) -> List[Dict]:
    """Current file_index docs for a repo, one per path."""
    rows = list(cluster.query(
        f"SELECT document_id, repo_id, file_path, commit_hash, content, metadata, "
        f"parent_id, children_ids, version FROM `{BUCKET}` "
        f"WHERE type = 'file_index' AND repo_id = $repo_id",
        repo_id=repo_id,
    ))
    return newest_per_key(rows, file_key)


def load_repo_module_docs(cluster, repo_id: str) -> List[Dict]:
    """Current module_summary docs for a repo, one per module path."""
    rows = list(cluster.query(
        f"SELECT document_id, repo_id, module_path, commit_hash, content, metadata, "
        f"parent_id, children_ids, version, quality FROM `{BUCKET}` "
        f"WHERE type = 'module_summary' AND repo_id = $repo_id",
        repo_id=repo_id,
    ))
    return newest_per_key(rows, module_key)


def resolve_module_children(
    file_docs: List[Dict],
    module_docs: List[Dict],
    module_path: str,
) -> Tuple[List[FileIndex], List[ModuleSummary]]:
    """Select the direct children of one module from a repo's current docs."""
    files = [
        rehydrate_file_index(d) for d in file_docs
        if is_direct_child_file(d.get("file_path") or "", module_path)
    ]
    files.sort(key=lambda f: f.file_path)

    submodules = [
        rehydrate_module_summary(d) for d in module_docs
        if is_direct_child_module(d.get("module_path") or "", module_path)
    ]
    submodules.sort(key=lambda m: m.module_path)
    return files, submodules
