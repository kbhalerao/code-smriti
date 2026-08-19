"""
Identifying the current version of a per-path document.

Historical: documents used to be keyed by commit, which made a re-ingest an
insert rather than an upsert. Superseded versions were cleaned up along one
narrow path only — a per-changed-file delete on the incremental route — so every
full reingest left a complete generation behind, and reading `file_index` back
for a repo yielded the same path many times over.

That is fixed at the source. `v4/schemas.py` now derives identity from location
alone, so re-processing a file overwrites its documents in place and generations
cannot accumulate. `scripts/migrate_stable_document_ids.py` moved the existing
corpus across.

These helpers remain for two jobs:

  - the migration itself, and any backfill that has to rank versions;
  - a cheap read-side guard in `doc_loader`, which is now a no-op against clean
    data but keeps aggregation correct if duplicates ever reappear.

The definition of "current" is unchanged and still per path, not per repo: a file
doc carries the commit at which that file was last processed, so unchanged files
legitimately sit at older commits than HEAD. Newest `version.created_at` wins,
ties broken on `document_id`.
"""

from typing import Callable, Dict, Iterable, List, Tuple


def _created_at(doc: Dict) -> str:
    """ISO-8601 creation stamp; sorts lexicographically, missing sorts oldest."""
    return ((doc.get("version") or {}).get("created_at")) or ""


def file_key(doc: Dict) -> Tuple[str, str]:
    return (doc.get("repo_id") or "", doc.get("file_path") or "")


def symbol_key(doc: Dict) -> Tuple[str, str, str]:
    return (
        doc.get("repo_id") or "",
        doc.get("file_path") or "",
        doc.get("symbol_name") or "",
    )


def newest_per_key(
    docs: Iterable[Dict],
    key: Callable[[Dict], tuple],
) -> List[Dict]:
    """
    Keep one doc per key — the most recently created.

    Ties break on `document_id` so the choice is deterministic across runs rather
    than dependent on query result ordering.
    """
    newest: Dict[tuple, Dict] = {}
    for doc in docs:
        k = key(doc)
        current = newest.get(k)
        if current is None:
            newest[k] = doc
            continue
        rank = (_created_at(doc), doc.get("document_id") or "")
        best = (_created_at(current), current.get("document_id") or "")
        if rank > best:
            newest[k] = doc
    return list(newest.values())


def partition_superseded(
    docs: Iterable[Dict],
    key: Callable[[Dict], tuple],
) -> Tuple[List[Dict], List[Dict]]:
    """
    Split docs into (current, superseded).

    Every key keeps exactly one doc, so the superseded list can never contain the
    last remaining version of a path — which is the property that makes deleting
    it safe.
    """
    docs = list(docs)
    current = newest_per_key(docs, key)
    # Identity, not document_id: a doc missing that field would otherwise match
    # every other doc missing it and survive deletion by accident.
    keep = {id(d) for d in current}
    superseded = [d for d in docs if id(d) not in keep]
    return current, superseded
