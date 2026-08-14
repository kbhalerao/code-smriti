"""
Identifying the current version of a per-path document.

Couchbase keys `file_index` and `symbol_index` docs by commit, and superseded
versions are only cleaned up along one narrow path: `updater._delete_old_file_docs`
runs per *changed* file, so a file that is not touched by a given run keeps its
old doc. That is correct for incremental runs, but a full reingest writes a fresh
doc for every file under a new commit without purging the previous set, so each
reingest leaves another complete generation behind.

The result is that reading `file_index` back for a repo yields several versions of
the same path. Anything that aggregates those rows sees one file many times over.

Unlike module/repo summaries, these docs cannot be anchored to a single "current
commit" per repo: a file doc carries the commit at which that file was last
processed, so unchanged files legitimately sit at older commits than HEAD. The
only sound definition of current is per path — newest `version.created_at` wins.
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
