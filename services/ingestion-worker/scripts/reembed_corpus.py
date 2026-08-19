#!/usr/bin/env python3
"""
Re-embed every vector in the corpus under the current embedding convention.

Required whenever `embeddings.convention` changes model, prefixing or
dimensionality, because a corpus with two embedding spaces in it does not fail —
it returns similarity scores that are numbers rather than measurements, and
nothing raises. There is no partial state worth having: either every vector
comes from the same model or search is quietly wrong.

Text construction mirrors `V4Pipeline.generate_embeddings` per type, since the
document must be embedded the same way it was when it was written:

    symbol_index / semantic_unit   summary + the symbol's source from disk
    file_index                     summary + a preview of the file from disk
    everything else                the document's own content

Source is re-read from disk rather than reconstructed, so a symbol whose file has
since changed is embedded against what the file says now — which is the same
thing the ingest pipeline would do today.

Safety:
    - Dry-run by default; --execute to write.
    - Writes only the `embedding` field, by subdocument mutation, so summaries,
      provenance and identity are untouched.
    - Resumable with --after: documents are processed in document_id order and
      the last id is logged, so an interrupted run continues rather than restarts.
    - The manifest is written last. Its absence is the signal that a re-embed did
      not finish, and the API's startup check reads it to catch a service still
      querying in the previous space.

Usage:
    ./.venv/bin/python scripts/reembed_corpus.py
    ./.venv/bin/python scripts/reembed_corpus.py --execute
    ./.venv/bin/python scripts/reembed_corpus.py --execute --after <document_id>
"""

import argparse
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import couchbase.subdocument as SD
from couchbase.options import QueryOptions
from loguru import logger

from config import WorkerConfig
from embeddings import convention
from embeddings.local_generator import LocalEmbeddingGenerator
from storage.couchbase_client import CouchbaseClient

BUCKET = "code_kosha"

# Every type carrying a vector in code_vector_index.
EMBEDDED_TYPES = [
    "symbol_index", "semantic_unit", "file_index", "module_summary",
    "repo_summary", "commit_index", "document", "spec", "repo_bdr", "code_chunk",
]
WITH_SOURCE = {"symbol_index", "semantic_unit"}
CAP = convention.CODE_CHARS_FOR_EMBEDDING


def repo_dir(repos: Path, repo_id: str) -> Path:
    return repos / (repo_id or "").replace("/", "_")


def embed_text(row: Dict, repos: Path, stats: Counter) -> str:
    """Rebuild the text this document was embedded from."""
    content = row.get("content") or ""
    doc_type = row.get("type")

    if doc_type not in WITH_SOURCE and doc_type != "file_index":
        return content

    path = repo_dir(repos, row.get("repo_id")) / (row.get("file_path") or "")
    if not path.is_file():
        stats["source_missing"] += 1
        return content
    try:
        text = path.read_text(errors="ignore")
    except Exception:
        stats["source_unreadable"] += 1
        return content

    if doc_type == "file_index":
        return f"{content}\n\nCode Preview:\n{text[:CAP]}"

    lines = text.split("\n")
    start, end = row.get("s") or 1, row.get("e") or 0
    snippet = "\n".join(lines[max(0, start - 1):end])[:CAP]
    return f"{content}\n\nCode:\n{snippet}" if snippet else content


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--after", help="resume from this document_id")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--page", type=int, default=2000)
    args = ap.parse_args()

    cb = CouchbaseClient()
    repos = Path(WorkerConfig().repos_path)
    generator = LocalEmbeddingGenerator() if args.execute else None

    total = list(cb.cluster.query(
        f"SELECT RAW COUNT(*) FROM `{BUCKET}` d WHERE d.type IN $t",
        QueryOptions(named_parameters={"t": EMBEDDED_TYPES}, timeout=timedelta(minutes=10)),
    ))[0]
    logger.info(
        f"{'RE-EMBEDDING' if args.execute else 'DRY RUN over'} {total:,} documents "
        f"with {convention.EMBEDDING_MODEL} at {convention.EMBEDDING_DIMS} dims"
    )

    stats = Counter()
    cursor = args.after or ""
    started = time.time()

    while True:
        rows = list(cb.cluster.query(
            f"""
            SELECT d.document_id, d.type, d.content, d.repo_id, d.file_path,
                   d.metadata.start_line AS s, d.metadata.end_line AS e
            FROM `{BUCKET}` d
            WHERE d.type IN $t AND d.document_id > $after
            ORDER BY d.document_id
            LIMIT $page
            """,
            QueryOptions(named_parameters={"t": EMBEDDED_TYPES, "after": cursor,
                                           "page": args.page},
                         timeout=timedelta(minutes=10)),
        ))
        if not rows:
            break

        texts = [embed_text(r, repos, stats) for r in rows]
        if args.execute:
            vectors = convention.encode_documents(generator.model, texts, batch_size=args.batch)
            for row, vector in zip(rows, vectors):
                try:
                    cb.collection.mutate_in(row["document_id"], [SD.upsert("embedding", vector)])
                    stats["written"] += 1
                except Exception as e:
                    logger.warning(f"  {row['document_id']}: {e}")
                    stats["write_failed"] += 1
        else:
            stats["would_write"] += len(rows)
        for row in rows:
            stats[row["type"]] += 1

        cursor = rows[-1]["document_id"]
        done = stats.get("written", stats.get("would_write", 0))
        rate = done / max(1e-6, time.time() - started)
        logger.info(f"  {done:,}/{total:,}  {rate:.0f}/s  "
                    f"eta {(total - done) / max(rate, 1e-6) / 60:.0f} min  "
                    f"cursor {cursor[:16]}")

    if args.execute and not stats["write_failed"]:
        # Written last, so a missing manifest means the run did not finish.
        manifest = convention.manifest(document_count=stats["written"])
        manifest["completed_at"] = datetime.now().isoformat()
        cb.collection.upsert("embedding_manifest", manifest)
        logger.info("wrote embedding_manifest")
    elif args.execute:
        logger.error("write failures occurred — manifest deliberately NOT written")

    logger.info("=" * 56)
    for key in sorted(stats):
        logger.info(f"  {key:<24} {stats[key]:>9,}")
    if not args.execute:
        logger.info("Dry run — nothing written. Re-run with --execute.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
