#!/usr/bin/env python3
"""
Create the symbol_index documents a pre-2026-08-14 parse never produced.

Background:
    Until `51d73f10` (2026-08-14) the Python parser recorded a class's extent as
    ending at the first line of its body rather than at the end of the class. Every
    class therefore looked about three lines long, which is below
    `SYMBOL_MIN_LINES`, so `SymbolRef.is_significant` was False and no
    `symbol_index` document was written for it. The methods *inside* the class were
    unaffected and got documents as normal, which is why the loss stayed hidden:
    per-file symbol counts looked nearly right.

    The parser is fixed, but incremental ingestion only reprocesses files that
    *change*, so 30,977 of 32,443 files still carry the pre-fix result. Measured
    2026-08-18: 16,095 classes are listed across `file_index.metadata.symbols`,
    only 4,166 are flagged significant and only 3,445 have a document.

    A sampled comparison against `ast.parse` put 68% of Python files short by ~3.7
    symbols, with the misses overwhelmingly classes whose methods were present.

What it does:
    Re-parses each file from disk through the same `FileProcessor.extract_symbols`
    the pipeline uses, then writes a document for every significant symbol that has
    none. It also refreshes `file_index.metadata.symbols` and `children_ids`, whose
    `significant` flags were computed from the same bad extents.

    Purely additive to the symbol table: it never deletes. Removing symbols that
    stopped existing is reconciliation, which the incremental path already does
    (`updater._reconcile_file_children`), and conflating the two here would make a
    backfill able to destroy data.

Safety:
    - Dry-run by default; pass --execute to write.
    - Idempotent and resumable: work is selected by *absence* of a document, so an
      interrupted run simply continues, and a completed run is a no-op.
    - `--repo` restricts the run; `--limit` caps files processed for a rehearsal.
    - Files missing from disk are counted and skipped, never guessed at.

Cost:
    Measured 2026-08-19: 12,228 missing symbols across 6,007 files, 10,533 of them
    classes. One LLM summary each, so run it in the background and expect hours;
    `--concurrency` controls how many files are in flight. Pass `--no-llm` to write
    docstring-derived summaries instead (`enrichment_level: basic`) — much faster,
    and a later run with the LLM enabled will not revisit them, so prefer the LLM
    unless you specifically want coverage before quality.

Usage:
    ./.venv/bin/python scripts/backfill_missing_class_symbols.py                      # dry-run
    ./.venv/bin/python scripts/backfill_missing_class_symbols.py --repo kbhalerao/ssurgo --execute
    ./.venv/bin/python scripts/backfill_missing_class_symbols.py --execute
"""

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import couchbase.subdocument as SD
from couchbase.options import QueryOptions
from loguru import logger

from config import WorkerConfig
from storage.couchbase_client import CouchbaseClient
from v4.pipeline import V4Pipeline
from v4.schemas import (
    SCHEMA_VERSION, EnrichmentLevel, QualityInfo, SymbolIndex, VersionInfo,
    make_content_hash, make_file_id, make_symbol_id,
)

BUCKET = "code_kosha"


def repo_dir(repos_path: Path, repo_id: str) -> Path:
    """Clones are stored with the slash in repo_id flattened to an underscore."""
    return repos_path / repo_id.replace("/", "_")


def fetch_files(cb: CouchbaseClient, repo_id: Optional[str]) -> List[Dict]:
    where = "d.type = 'file_index'"
    params: Dict[str, object] = {}
    if repo_id:
        where += " AND d.repo_id = $repo_id"
        params["repo_id"] = repo_id
    query = f"""
        SELECT d.document_id, d.repo_id, d.file_path, d.commit_hash,
               d.metadata.`language` AS lang, d.children_ids
        FROM `{BUCKET}` d
        WHERE {where}
    """
    return list(cb.cluster.query(query, QueryOptions(named_parameters=params)))


def _child_ids(cb: CouchbaseClient, doc_type: str, repo_id: str, file_path: str) -> set:
    query = f"""
        SELECT RAW d.document_id FROM `{BUCKET}` d
        WHERE d.type = $doc_type AND d.repo_id = $repo_id AND d.file_path = $file_path
    """
    return set(cb.cluster.query(query, QueryOptions(named_parameters={
        "doc_type": doc_type, "repo_id": repo_id, "file_path": file_path,
    })))


def existing_symbol_ids(cb: CouchbaseClient, repo_id: str, file_path: str) -> set:
    return _child_ids(cb, "symbol_index", repo_id, file_path)


def semantic_unit_ids(cb: CouchbaseClient, repo_id: str, file_path: str) -> set:
    return _child_ids(cb, "semantic_unit", repo_id, file_path)


async def backfill_file(
    pipeline: V4Pipeline,
    cb: CouchbaseClient,
    row: Dict,
    repos_path: Path,
    execute: bool,
    stats: Counter,
) -> None:
    repo_id, file_path = row["repo_id"], row["file_path"]
    disk = repo_dir(repos_path, repo_id) / file_path
    if not disk.exists():
        stats["file_absent_on_disk"] += 1
        return

    try:
        content = disk.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        stats["file_unreadable"] += 1
        return

    fp = pipeline.file_processor
    language = row.get("lang") or fp.code_parser.detect_language(disk)
    symbols = await fp.extract_symbols(file_path, content, language)
    significant = [s for s in symbols if s.is_significant]
    if not significant:
        return

    have = existing_symbol_ids(cb, repo_id, file_path)
    missing = [
        s for s in significant
        if make_symbol_id(repo_id, file_path, s.name) not in have
    ]
    if not missing:
        stats["files_already_complete"] += 1
        return

    stats["files_with_gaps"] += 1
    for s in missing:
        stats[f"missing_{s.symbol_type}"] += 1
    if not execute:
        stats["would_create"] += len(missing)
        return

    file_doc_id = make_file_id(repo_id, file_path)
    created = 0
    for symbol in missing:
        snippet = fp.get_code_snippet(content, symbol.start_line, symbol.end_line)
        try:
            summary, level = await fp.generate_symbol_summary(symbol, snippet, file_path, language)
        except Exception as e:
            logger.warning(f"  {repo_id}:{file_path}:{symbol.name} summary failed: {e}")
            stats["summary_failed"] += 1
            continue

        doc = SymbolIndex(
            document_id=make_symbol_id(repo_id, file_path, symbol.name),
            repo_id=repo_id,
            file_path=file_path,
            commit_hash=row.get("commit_hash") or "",
            symbol_name=symbol.name,
            symbol_type=symbol.symbol_type,
            language=language or "",
            content=summary,
            start_line=symbol.start_line,
            end_line=symbol.end_line,
            docstring=symbol.docstring,
            methods=symbol.methods,
            content_hash=make_content_hash(snippet),
            parent_id=file_doc_id,
            quality=QualityInfo(
                enrichment_level=level,
                llm_available=fp.quality_tracker.llm_available,
                summary_source=(
                    "backfill_class_extent_llm"
                    if level == EnrichmentLevel.LLM_SUMMARY
                    else "backfill_class_extent_docstring"
                ),
            ),
            version=VersionInfo(
                schema_version=SCHEMA_VERSION,
                pipeline_version=datetime.now().strftime("%Y.%m.%d"),
                created_at=datetime.now().isoformat(),
            ),
        )
        if pipeline.embedding_generator:
            doc.embedding = pipeline.embedding_generator.generate_embedding(
                f"{summary}\n\nCode:\n{snippet[:2000]}"
            )
        try:
            cb.collection.upsert(doc.document_id, doc.to_dict())
            created += 1
        except Exception as e:
            logger.warning(f"  {repo_id}:{file_path}:{symbol.name} write failed: {e}")
            stats["write_failed"] += 1

    stats["symbols_created"] += created

    # The file's own symbol list carries `significant` flags derived from the same
    # bad extents, so refresh it alongside the documents it describes.
    #
    # children_ids is rebuilt from the current parse plus the file's semantic
    # units, which are queried rather than carried over from the old list: the old
    # list predates the identity migration and can hold ids that no longer exist.
    try:
        child_ids = sorted(
            {make_symbol_id(repo_id, file_path, s.name) for s in significant}
            | semantic_unit_ids(cb, repo_id, file_path)
        )
        cb.collection.mutate_in(file_doc_id, [
            SD.upsert("metadata.symbols", [s.to_dict() for s in symbols]),
            SD.upsert("children_ids", child_ids),
        ])
        stats["file_metadata_refreshed"] += 1
    except Exception as e:
        logger.warning(f"  {repo_id}:{file_path} metadata refresh failed: {e}")
        stats["metadata_refresh_failed"] += 1


async def run(args) -> Counter:
    config = WorkerConfig()
    cb = CouchbaseClient()
    pipeline = V4Pipeline(
        enable_llm=not args.no_llm,
        enable_embeddings=args.execute,
        dry_run=False,
    )
    rows = fetch_files(cb, args.repo)
    if args.limit:
        rows = rows[: args.limit]
    logger.info(
        f"{'BACKFILLING' if args.execute else 'DRY RUN over'} {len(rows)} files"
        f"{' (LLM disabled)' if args.no_llm else ''}"
    )

    stats = Counter()
    repos_path = Path(config.repos_path)
    done = {"n": 0}

    # Files run concurrently because the cost is dominated by one LLM call per
    # missing symbol, and those are network-bound. Embedding generation is a
    # synchronous call that blocks the loop, which is fine — it is milliseconds
    # against seconds for the summary.
    semaphore = asyncio.Semaphore(args.concurrency)

    async def one(row: Dict) -> None:
        async with semaphore:
            try:
                await backfill_file(pipeline, cb, row, repos_path, args.execute, stats)
            except Exception as e:
                logger.warning(f"  {row.get('repo_id')}:{row.get('file_path')} failed: {e}")
                stats["file_failed"] += 1
            done["n"] += 1
            if done["n"] % 500 == 0:
                logger.info(
                    f"  [{done['n']}/{len(rows)}] gaps in {stats['files_with_gaps']} files, "
                    f"{stats.get('symbols_created', stats.get('would_create', 0))} symbols"
                )

    await asyncio.gather(*(one(r) for r in rows))
    await pipeline.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--execute", action="store_true", help="actually write; default is a dry run")
    ap.add_argument("--repo", help="restrict to one repo_id")
    ap.add_argument("--limit", type=int, help="cap files processed, for a rehearsal")
    ap.add_argument(
        "--concurrency", type=int, default=6,
        help="files processed in parallel (default 6); the LLM is the bottleneck",
    )
    ap.add_argument(
        "--no-llm", action="store_true",
        help="write docstring-derived summaries instead of calling the LLM",
    )
    args = ap.parse_args()

    stats = asyncio.run(run(args))

    logger.info("=" * 60)
    for k in sorted(stats):
        logger.info(f"  {k:<34} {stats[k]:>8}")
    if not args.execute:
        logger.info("Dry run — nothing was written. Re-run with --execute to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
