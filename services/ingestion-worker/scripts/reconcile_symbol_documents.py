#!/usr/bin/env python3
"""
Bring every file's symbol documents back in line with what its current parse says.

Three defects share one cause — the stored symbol layer was written by an older
parse and nothing ever re-read the file — so one sweep fixes all three:

  1. Anonymous symbols collapsed onto a shared id. `make_symbol_id` keyed on
     (repo, path, name), and the parser writes the placeholder `anonymous` or
     `arrow_function` when it cannot name a symbol. Every such symbol in a file
     therefore hashed to the same id and overwrote its predecessors: 21,055
     symbols corpus-wide kept only whichever one was written last (12,084 arrow
     functions, 8,971 `anonymous`). A further ~1,300 real names collide the same
     way — overloads and repeated delegate methods (`tableView` 38 times in one
     file, `init` 22). `assign_symbol_ids` now falls back to the span for both
     cases, and this script moves the corpus onto those ids.

  2. Stale `file_index.metadata.symbols`. The listing carries `significant` flags
     computed from the pre-51d73f10 class extents, so it disagrees with the
     documents beside it on 5,214 files. Re-parsing is what refreshes it.

  3. Symbols with no document at all — what
     `backfill_missing_class_symbols.py` did for the class-extent bug, as the
     general case. That script is superseded by this one.

Salvage:
    A re-key is not a re-summarise. When an existing document has the same name
    and the same span as a symbol whose id changed, its summary is carried over
    to the new id and only the embedding is recomputed (local, milliseconds). The
    LLM is called only for symbols that genuinely never had a document — for an
    anonymous group of N that is N-1 of them, since only one was ever written.

Safety:
    - Dry-run by default; --execute to write.
    - New ids are written before old ones are removed, never the reverse, so an
      interrupted run leaves duplicates that the next run reconciles rather than
      a file with no symbols.
    - Deletion only ever removes ids absent from the current parse, and is
      skipped entirely for a file whose parse yields no significant symbols. That
      case is indistinguishable from a parser regression, and this script is not
      the right place to find out which it is; it counts them as `parse_empty`.
    - Idempotent: a second run over unchanged files is queries and no writes.

Usage:
    ./.venv/bin/python scripts/reconcile_symbol_documents.py                     # dry run
    ./.venv/bin/python scripts/reconcile_symbol_documents.py --repo o/r --execute
    ./.venv/bin/python scripts/reconcile_symbol_documents.py --execute --concurrency 8
"""

import argparse
import asyncio
import sys
from collections import Counter
from datetime import datetime, timedelta
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
    assign_symbol_ids, make_content_hash, make_file_id,
)

BUCKET = "code_kosha"
LONG = QueryOptions(timeout=timedelta(minutes=10))


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
        SELECT d.repo_id, d.file_path, d.commit_hash,
               d.metadata.`language` AS lang
        FROM `{BUCKET}` d
        WHERE {where}
    """
    opts = QueryOptions(named_parameters=params, timeout=timedelta(minutes=10))
    return list(cb.cluster.query(query, opts))


def symbols_by_file(cb: CouchbaseClient, repo_id: str) -> Dict[str, List[Dict]]:
    """
    One repo's symbol documents, grouped by path, without their embeddings.

    Fetched per repo rather than per file: a query per file is 32,443 round trips
    for a full sweep and dominates the run when the LLM has nothing to do.

    `start_line`/`end_line` live under `metadata`, not at the top level, and N1QL
    returns MISSING rather than erroring for the wrong path — which silently made
    every salvage key (name, None, None) and matched nothing, so a re-key paid for
    a fresh summary it already had.

    The embedding is 768 floats and is never salvaged — it is regenerated from the
    summary and the code, which costs milliseconds and cannot drift from them.
    """
    query = f"""
        SELECT d.file_path, d.document_id, d.symbol_name, d.symbol_type, d.content,
               d.metadata.start_line AS start_line,
               d.metadata.end_line AS end_line,
               d.metadata.docstring AS docstring, d.quality
        FROM `{BUCKET}` d
        WHERE d.type = 'symbol_index' AND d.repo_id = $r
    """
    out: Dict[str, List[Dict]] = {}
    for row in cb.cluster.query(query, QueryOptions(
        named_parameters={"r": repo_id}, timeout=timedelta(minutes=10)
    )):
        out.setdefault(row.pop("file_path"), []).append(row)
    return out


def semantic_units_by_file(cb: CouchbaseClient, repo_id: str) -> Dict[str, set]:
    query = f"""
        SELECT d.file_path, d.document_id FROM `{BUCKET}` d
        WHERE d.type = 'semantic_unit' AND d.repo_id = $r
    """
    out: Dict[str, set] = {}
    for row in cb.cluster.query(query, QueryOptions(
        named_parameters={"r": repo_id}, timeout=timedelta(minutes=10)
    )):
        out.setdefault(row["file_path"], set()).add(row["document_id"])
    return out


def build_doc(repo_id, file_path, commit_hash, doc_id, symbol, language,
              summary, level, source, snippet) -> SymbolIndex:
    return SymbolIndex(
        document_id=doc_id,
        repo_id=repo_id,
        file_path=file_path,
        commit_hash=commit_hash or "",
        symbol_name=symbol.name,
        symbol_type=symbol.symbol_type,
        language=language or "",
        content=summary,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        docstring=symbol.docstring,
        methods=symbol.methods,
        content_hash=make_content_hash(snippet),
        parent_id=make_file_id(repo_id, file_path),
        quality=QualityInfo(
            enrichment_level=level,
            llm_available=True,
            summary_source=source,
        ),
        version=VersionInfo(
            schema_version=SCHEMA_VERSION,
            pipeline_version=datetime.now().strftime("%Y.%m.%d"),
            created_at=datetime.now().isoformat(),
        ),
    )


async def reconcile_file(pipeline, cb, row, repos_path, execute, stats,
                         repo_symbols, repo_units) -> None:
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
    ids = assign_symbol_ids(repo_id, file_path, symbols)
    wanted = {i: s for i, s in zip(ids, symbols) if s.is_significant}

    existing = repo_symbols.get(file_path, [])
    have = {d["document_id"]: d for d in existing}

    if not wanted:
        # Never let a parse that found nothing empty out a file's symbol layer.
        if existing:
            stats["parse_empty_kept"] += 1
        return

    missing = {i: s for i, s in wanted.items() if i not in have}
    superseded = [d for d in existing if d["document_id"] not in wanted]

    # A document describing the same name at the same span is this symbol under
    # its old id: carry the summary across rather than paying for it again.
    by_span = {
        (d.get("symbol_name"), d.get("start_line"), d.get("end_line")): d
        for d in superseded
    }

    if not missing and not superseded:
        stats["files_already_consistent"] += 1
    else:
        stats["files_changed"] += 1
        for i, s in missing.items():
            key = (s.name, s.start_line, s.end_line)
            stats["salvageable" if key in by_span else "needs_llm"] += 1
        stats["superseded_ids"] += len(superseded)

    if not execute:
        stats["would_create"] += len(missing)
        stats["would_delete"] += len(superseded)
        return

    written = set()
    for doc_id, symbol in missing.items():
        snippet = fp.get_code_snippet(content, symbol.start_line, symbol.end_line)
        prior = by_span.get((symbol.name, symbol.start_line, symbol.end_line))
        if prior and prior.get("content"):
            quality = prior.get("quality") or {}
            summary = prior["content"]
            # Round-tripped through Couchbase the level is a plain string, and
            # QualityInfo.to_dict() calls .value on it.
            try:
                level = EnrichmentLevel(quality.get("enrichment_level"))
            except ValueError:
                level = EnrichmentLevel.LLM_SUMMARY
            source = quality.get("summary_source") or "rekeyed"
            stats["salvaged"] += 1
        else:
            try:
                summary, level = await fp.generate_symbol_summary(
                    symbol, snippet, file_path, language
                )
            except Exception as e:
                logger.warning(f"  {repo_id}:{file_path}:{symbol.name} summary failed: {e}")
                stats["summary_failed"] += 1
                continue
            source = (
                "reconcile_span_identity_llm"
                if level == EnrichmentLevel.LLM_SUMMARY
                else "reconcile_span_identity_docstring"
            )
        doc = build_doc(repo_id, file_path, row.get("commit_hash"), doc_id, symbol,
                        language, summary, level, source, snippet)
        if pipeline.embedding_generator:
            doc.embedding = pipeline.embedding_generator.generate_embedding(
                f"{summary}\n\nCode:\n{snippet[:2000]}"
            )
        try:
            cb.collection.upsert(doc_id, doc.to_dict())
            written.add(doc_id)
            stats["symbols_created"] += 1
        except Exception as e:
            logger.warning(f"  {repo_id}:{file_path}:{symbol.name} write failed: {e}")
            stats["write_failed"] += 1

    # Only now, with the replacements durable, drop what the current parse does
    # not produce. A doc whose write failed above keeps its predecessor.
    for d in superseded:
        key = (d.get("symbol_name"), d.get("start_line"), d.get("end_line"))
        replacement = next((i for i, s in missing.items()
                            if (s.name, s.start_line, s.end_line) == key), None)
        if replacement is not None and replacement not in written:
            stats["delete_held_back"] += 1
            continue
        try:
            cb.collection.remove(d["document_id"])
            stats["symbols_deleted"] += 1
        except Exception as e:
            logger.warning(f"  {repo_id}:{file_path} delete failed: {e}")
            stats["delete_failed"] += 1

    try:
        child_ids = sorted(set(wanted) | repo_units.get(file_path, set()))
        cb.collection.mutate_in(make_file_id(repo_id, file_path), [
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
    logger.info(f"{'RECONCILING' if args.execute else 'DRY RUN over'} {len(rows)} files")

    stats = Counter()
    repos_path = Path(config.repos_path)
    done = {"n": 0}
    semaphore = asyncio.Semaphore(args.concurrency)

    by_repo: Dict[str, List[Dict]] = {}
    for row in rows:
        by_repo.setdefault(row["repo_id"], []).append(row)

    async def one(row, repo_symbols, repo_units) -> None:
        async with semaphore:
            try:
                await reconcile_file(pipeline, cb, row, repos_path, args.execute,
                                     stats, repo_symbols, repo_units)
            except Exception as e:
                logger.warning(f"  {row.get('repo_id')}:{row.get('file_path')} failed: {e}")
                stats["file_failed"] += 1
            done["n"] += 1
            if done["n"] % 500 == 0:
                logger.info(
                    f"  [{done['n']}/{len(rows)}] {stats['files_changed']} files changed, "
                    f"{stats.get('symbols_created', stats.get('would_create', 0))} created, "
                    f"{stats.get('symbols_deleted', stats.get('would_delete', 0))} deleted"
                )

    # One repo at a time so the existing documents can be read in two queries
    # instead of two per file. Files within a repo still run concurrently.
    for n, (repo_id, repo_rows) in enumerate(sorted(by_repo.items()), 1):
        try:
            repo_symbols = symbols_by_file(cb, repo_id)
            repo_units = semantic_units_by_file(cb, repo_id)
        except Exception as e:
            logger.warning(f"{repo_id}: could not read existing documents: {e}")
            stats["repo_read_failed"] += 1
            continue
        logger.info(f"[{n}/{len(by_repo)}] {repo_id} ({len(repo_rows)} files)")
        await asyncio.gather(*(one(r, repo_symbols, repo_units) for r in repo_rows))

    await pipeline.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--execute", action="store_true", help="actually write; default is a dry run")
    ap.add_argument("--repo", help="restrict to one repo_id")
    ap.add_argument("--limit", type=int, help="cap files processed, for a rehearsal")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="files in flight (default 6); the LLM is the bottleneck")
    ap.add_argument("--no-llm", action="store_true",
                    help="docstring-derived summaries instead of calling the LLM")
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
