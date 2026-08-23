#!/usr/bin/env python3
"""
Create the GSI indexes the query paths depend on, idempotently.

Without these the inspector is unusable rather than merely slow: listing
repositories took twelve seconds against 180,000 documents, which the page spent
showing an empty picker with no indication anything was happening.

The recurring lesson is in `idx_repo_summary_list`. An index is only *considered*
when the query constrains its leading key, so `WHERE type = 'repo_summary'` alone
ignored it and fell back to a full scan; adding `AND repo_id IS NOT MISSING` took
the same query from 9.9s to effectively instant. A partial index whose leading
key is never constrained is dead weight.

Safe to run against a live cluster. Each statement is `IF NOT EXISTS`, and the
partial indexes here are small enough to build in seconds — these were all built
while a corpus-wide reconciliation was writing.

Usage:
    ./.venv/bin/python scripts/ensure_query_indexes.py           # report only
    ./.venv/bin/python scripts/ensure_query_indexes.py --execute
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from couchbase.options import QueryOptions
from datetime import timedelta
from loguru import logger

from storage.couchbase_client import CouchbaseClient

BUCKET = "code_kosha"

INDEXES: list[tuple[str, str, str]] = [
    (
        "idx_ingestion_queue",
        "claiming the longest-waiting queued repo, every drain tick",
        f"""CREATE INDEX IF NOT EXISTS idx_ingestion_queue ON `{BUCKET}`
            (state, enqueued_at, attempts)
            WHERE type = 'ingestion_queue_item'""",
    ),
    (
        "idx_ingestion_dlq",
        "the dead-letter queue, read after every run and by --dlq",
        f"""CREATE INDEX IF NOT EXISTS idx_ingestion_dlq ON `{BUCKET}`
            (last_seen DESC, repo_id)
            WHERE type = 'ingestion_dlq'""",
    ),
    (
        "idx_repo_summary_list",
        "listing every repository for the inspector's picker",
        f"""CREATE INDEX IF NOT EXISTS idx_repo_summary_list ON `{BUCKET}`
            (repo_id, metadata.total_files, metadata.total_lines)
            WHERE type = 'repo_summary'""",
    ),
    (
        "idx_file_by_repo",
        "one repository's files, with language, for the tree",
        f"""CREATE INDEX IF NOT EXISTS idx_file_by_repo ON `{BUCKET}`
            (repo_id, file_path, document_id, metadata.`language`)
            WHERE type = 'file_index'""",
    ),
    (
        "idx_module_by_repo",
        "one repository's module summaries",
        f"""CREATE INDEX IF NOT EXISTS idx_module_by_repo ON `{BUCKET}`
            (repo_id, module_path, document_id)
            WHERE type = 'module_summary'""",
    ),
    (
        "idx_child_by_repo_file",
        "per-file symbol and semantic-unit counts",
        f"""CREATE INDEX IF NOT EXISTS idx_child_by_repo_file ON `{BUCKET}`
            (repo_id, file_path, type)
            WHERE type IN ['symbol_index', 'semantic_unit']""",
    ),
    (
        "idx_annotation_target",
        "judgments on one document",
        f"""CREATE INDEX IF NOT EXISTS idx_annotation_target ON `{BUCKET}`
            (target_id, created_at)
            WHERE type = 'annotation'""",
    ),
    (
        "idx_annotation_repo",
        "judgments across one repository, and the verdict stats",
        f"""CREATE INDEX IF NOT EXISTS idx_annotation_repo ON `{BUCKET}`
            (anchor.repo_id, created_at)
            WHERE type = 'annotation'""",
    ),
]


def existing(cb: CouchbaseClient) -> set:
    rows = cb.cluster.query(
        "SELECT RAW name FROM system:indexes WHERE keyspace_id = $b",
        QueryOptions(named_parameters={"b": BUCKET}, timeout=timedelta(minutes=2)),
    )
    return set(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="create missing indexes")
    args = ap.parse_args()

    cb = CouchbaseClient()
    present = existing(cb)

    for name, purpose, ddl in INDEXES:
        if name in present:
            logger.info(f"  {name:<24} present    — {purpose}")
            continue
        if not args.execute:
            logger.info(f"  {name:<24} MISSING    — {purpose}")
            continue
        start = time.time()
        cb.cluster.query(ddl, QueryOptions(timeout=timedelta(minutes=15))).execute()
        logger.info(f"  {name:<24} built {time.time() - start:5.1f}s — {purpose}")

    if not args.execute:
        logger.info("Report only — re-run with --execute to create anything missing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
