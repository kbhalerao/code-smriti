#!/usr/bin/env python3
"""
Purge superseded file_index and symbol_index documents.

Background:
    These docs are keyed by commit. `updater._delete_old_file_docs` clears prior
    versions of a file, but only for files *changed* in that run — which is right
    for incremental updates, since an unchanged file's doc is still accurate. The
    gap is the full-reingest path: it writes a fresh doc for every file under a
    new commit and purges nothing, so each reingest leaves a whole generation
    behind. Measured 2026-08-13: 20,403 superseded file_index (39% of the type)
    and 42,271 superseded symbol_index (31%), the oldest dating to 2025-11-29.

    They are not inert. They sit in the FTS vector index, so retrieval returns
    summaries of code that no longer exists, and anything aggregating file docs
    counts one file many times over — `tier1apps/gislayers` has 7 files and its
    module summary recorded 21.

Definition of "current":
    Per path, not per repo. Unlike module/repo summaries there is no single
    current commit to anchor on: a file doc carries the commit at which that file
    was last processed, so unchanged files legitimately sit behind HEAD. Newest
    `version.created_at` wins, ties broken on document_id for determinism.
    See v4/doc_versions.py.

Safety:
    - Dry-run by default; pass --execute to delete.
    - Exactly one doc is kept per key, so the last remaining version of a path
      can never be deleted — that invariant comes from partition_superseded, not
      from the delete query.
    - Docs missing repo_id/file_path are counted and left untouched rather than
      grouped under an empty key.
    - --repo restricts the whole run to one repository, for a rehearsal before
      the full sweep.

Usage:
    ./.venv/bin/python scripts/purge_superseded_file_docs.py                     # dry-run, all repos
    ./.venv/bin/python scripts/purge_superseded_file_docs.py --repo kbhalerao/agkit.io-backend
    ./.venv/bin/python scripts/purge_superseded_file_docs.py --execute           # delete
"""

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from couchbase.options import QueryOptions
from loguru import logger

from storage.couchbase_client import CouchbaseClient
from v4.doc_versions import file_key, partition_superseded, symbol_key

BUCKET = "code_kosha"
DELETE_BATCH = 500

# (doc type, key function, extra projected field)
TARGETS = (
    ("file_index", file_key, None),
    ("symbol_index", symbol_key, "symbol_name"),
)


def fetch_docs(cb: CouchbaseClient, doc_type: str, extra: Optional[str], repo_id: str) -> List[Dict]:
    """Project only the fields needed to rank versions — never whole documents."""
    fields = ["document_id", "repo_id", "file_path", "commit_hash", "version"]
    if extra:
        fields.append(extra)
    where = f"type = '{doc_type}'"
    params = {}
    if repo_id:
        where += " AND repo_id = $repo_id"
        params["repo_id"] = repo_id

    query = f"SELECT {', '.join(fields)} FROM `{BUCKET}` WHERE {where}"
    result = cb.cluster.query(query, QueryOptions(named_parameters=params)) if params \
        else cb.cluster.query(query)
    return list(result)


def delete_by_ids(cb: CouchbaseClient, doc_ids: List[str]) -> int:
    """Delete in batches by key. N1QL is lazy — the result MUST be consumed."""
    deleted = 0
    for start in range(0, len(doc_ids), DELETE_BATCH):
        batch = doc_ids[start:start + DELETE_BATCH]
        result = cb.cluster.query(
            f"DELETE FROM `{BUCKET}` USE KEYS $ids",
            QueryOptions(named_parameters={"ids": batch}),
        )
        _ = list(result)  # consume to force execution
        deleted += len(batch)
        logger.info(f"    deleted {deleted:,}/{len(doc_ids):,}")
    return deleted


def process(
    cb: CouchbaseClient,
    doc_type: str,
    key: Callable[[Dict], tuple],
    extra: Optional[str],
    repo_id: str,
    execute: bool,
) -> tuple:
    logger.info(f"\n=== {doc_type} ===")
    docs = fetch_docs(cb, doc_type, extra, repo_id)
    logger.info(f"  fetched {len(docs):,} docs")

    usable, unkeyed = [], 0
    for d in docs:
        if d.get("repo_id") and d.get("file_path") and d.get("document_id"):
            usable.append(d)
        else:
            unkeyed += 1

    current, superseded = partition_superseded(usable, key)
    logger.info(f"  current: {len(current):,}   superseded: {len(superseded):,}")
    if unkeyed:
        logger.warning(f"  {unkeyed:,} doc(s) missing repo_id/file_path/document_id — left untouched")

    # partition_superseded guarantees one survivor per key by construction, but
    # this is a delete against the live bucket: assert the property against the
    # actual partition rather than trusting the implementation that produced it.
    # A key appearing in `superseded` but not `current` would mean deleting a
    # path's last remaining document.
    orphaned = {key(d) for d in superseded} - {key(d) for d in current}
    if orphaned:
        logger.error(
            f"  ABORT: {len(orphaned)} key(s) would lose their last version, "
            f"e.g. {sorted(orphaned)[:3]}"
        )
        raise SystemExit(2)

    if superseded:
        by_repo = Counter(d.get("repo_id", "?") for d in superseded)
        for repo, n in by_repo.most_common(8):
            logger.info(f"    {repo}: {n:,}")

    if superseded and execute:
        delete_by_ids(cb, [d["document_id"] for d in superseded])

    return len(current), len(superseded), unkeyed


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge superseded file/symbol docs")
    ap.add_argument("--execute", action="store_true", help="Actually delete (default is dry-run)")
    ap.add_argument("--repo", default="", help="Restrict to a single repo_id")
    args = ap.parse_args()

    cb = CouchbaseClient()
    mode = "EXECUTE" if args.execute else "DRY-RUN"
    scope = args.repo or "all repos"
    logger.info(f"Mode: {mode}   Scope: {scope}")

    total_current = total_superseded = total_unkeyed = 0
    for doc_type, key, extra in TARGETS:
        cur, sup, unk = process(cb, doc_type, key, extra, args.repo, args.execute)
        total_current += cur
        total_superseded += sup
        total_unkeyed += unk

    logger.info(f"\n{'=' * 60}")
    logger.info(f"TOTAL current: {total_current:,}   superseded: {total_superseded:,}")
    if total_unkeyed:
        logger.warning(f"TOTAL unkeyed (untouched): {total_unkeyed:,}")
    if not args.execute and total_superseded:
        logger.warning("Dry-run — nothing deleted. Re-run with --execute to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
