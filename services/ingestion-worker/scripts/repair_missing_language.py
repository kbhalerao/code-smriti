#!/usr/bin/env python3
"""
Set the `language` field on documents written before the pipeline recorded it.

Measured 2026-08-19: 10,699 `symbol_index` and 4,251 `semantic_unit` documents
have no `language` at all — the field is MISSING, not null and not empty, so
these predate the schema that writes it rather than having failed to detect it.
`file_index` is unaffected; it carries the language under `metadata`.

They are ordinary Python, JavaScript, TypeScript and Svelte files, so the value
is recoverable from the path alone: every one of the 14,950 resolves through the
same `CodeParser.detect_language` the pipeline uses, with none left over. That is
why this is a field write rather than a re-parse — no tree-sitter, no LLM, and
nothing about the summaries changes.

Why it matters beyond tidiness: a document with no language cannot be routed to a
grammar for fingerprinting, cannot be filtered by language when sampling for
evaluation, and cannot be stratified into a per-language training set. It is a
silent hole in every downstream use of the corpus.

The current write path always emits the field, so this is a one-time repair of
legacy documents and not a recurring sweep.

Safety:
    - Dry-run by default; --execute to write.
    - Uses a subdocument mutation, so the 768-float embedding is never fetched or
      rewritten — only the one field changes.
    - Idempotent: work is selected by the field's absence, so a completed run is a
      no-op and an interrupted one continues.

Usage:
    ./.venv/bin/python scripts/repair_missing_language.py
    ./.venv/bin/python scripts/repair_missing_language.py --execute
"""

import argparse
import sys
from collections import Counter
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import couchbase.subdocument as SD
from couchbase.options import QueryOptions
from loguru import logger

from parsers.code_parser import CodeParser
from storage.couchbase_client import CouchbaseClient

BUCKET = "code_kosha"
DOC_TYPES = ("symbol_index", "semantic_unit")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="write; default is a dry run")
    args = ap.parse_args()

    cb = CouchbaseClient()
    # detect_language reads no instance state, so the parser is not constructed —
    # building one loads every tree-sitter grammar for a pure path lookup.
    detect = CodeParser.detect_language

    rows = list(cb.cluster.query(
        f"""
        SELECT d.document_id, d.file_path, d.type FROM `{BUCKET}` d
        WHERE d.type IN $types AND d.`language` IS MISSING
        """,
        QueryOptions(named_parameters={"types": list(DOC_TYPES)},
                     timeout=timedelta(minutes=10)),
    ))
    logger.info(f"{'REPAIRING' if args.execute else 'DRY RUN over'} {len(rows)} documents")

    stats = Counter()
    for row in rows:
        language = detect(None, Path(row.get("file_path") or ""))
        if not language:
            # Never guess. A path whose extension is unknown to the pipeline is
            # left alone and counted, so it stays visible instead of being filled
            # with something plausible.
            stats["unrecoverable"] += 1
            continue
        stats[language] += 1
        if not args.execute:
            continue
        try:
            cb.collection.mutate_in(row["document_id"], [SD.upsert("language", language)])
            stats["written"] += 1
        except Exception as e:
            logger.warning(f"  {row['document_id']}: {e}")
            stats["write_failed"] += 1

    logger.info("=" * 52)
    for key in sorted(stats):
        logger.info(f"  {key:<20} {stats[key]:>7}")
    if not args.execute:
        logger.info("Dry run — nothing written. Re-run with --execute to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
