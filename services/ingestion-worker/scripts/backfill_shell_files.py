#!/usr/bin/env python3
"""
Index shell scripts that predate `.sh` being a supported extension.

`.sh`, `.bash` and `.zsh` were added to `supported_code_extensions` (and to
`CodeParser.detect_language`) on 2026-08-22. That change on its own reaches
almost nothing, because incremental ingestion is change-driven: a shell script
is only picked up if someone happens to edit it. Every other one stays invisible
to search indefinitely. The same trap swallowed the class-extraction parser fix,
which was correct for weeks while the corpus knew nothing about it — a parser
change without a backfill is a change to future runs only.

Raising DEFAULT_REINGEST_THRESHOLD to 0.5 the same day makes this worse, not
better: accidental full re-ingests were the only thing that would have swept
these in eventually, and there are now far fewer of them.

So this walks each repo for shell files the corpus is missing and processes just
those, through the same `file_processor.process` the incremental path uses. It
does not re-ingest anything else and does not move the commits index — the repo
is still indexed at the same commit, it just has files it should have had.

Module and repo summaries are NOT regenerated unless --regenerate-summaries is
passed. They are aggregates over file_index and will pick these up on the repo's
next real change; regenerating them here would mean an LLM call per module
across 81 repos for a marginal gain.

Dry run unless --apply.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from loguru import logger

SHELL_EXTENSIONS = (".sh", ".bash", ".zsh")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write documents (default: dry run)")
    ap.add_argument("--repo", default=None, help="limit to one repo_id")
    ap.add_argument("--limit", type=int, default=None, help="stop after N repos")
    ap.add_argument("--regenerate-summaries", action="store_true",
                    help="also regenerate module/repo summaries for touched repos")
    ap.add_argument("--force", action="store_true",
                    help="run even while a scheduled ingestion holds the lock")
    args = ap.parse_args()

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")

    from parsers.code_parser import should_skip_file
    from v4.incremental.runner import check_running
    from v4.incremental.repo_lifecycle import REPO_COMMITS_INDEX_DOC_ID

    # Ingestion is GPU-bound at ~94% utilisation, so writing alongside it makes
    # both slower. The guard is on --apply only: a dry run reads Couchbase and
    # the filesystem, competes for nothing, and is exactly what you want to be
    # able to run while a scheduled ingestion is in flight.
    lock_fd = None
    if args.apply and not args.force:
        running = check_running()
        if running:
            print(f"A scheduled ingestion is running (pid {running.get('pid')}, "
                  f"started {running.get('started')}). Re-run when it finishes, or --force.")
            return 1
        # Hold the ingestion lock for the duration. This backfill is GPU-heavy
        # corpus mutation, which is precisely what the lock serialises — without
        # it the 15:05 LaunchAgent would start midway and the two would halve
        # each other's throughput. run_incremental.sh treats a held lock as a
        # clean skip (exit 2, no alert), so the scheduled run simply waits a day.
        import fcntl
        from v4.incremental.runner import IngestionRunner
        lock_fd = open(IngestionRunner.LOCK_FILE, "a+")
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_fd.close()
            print("Could not take the ingestion lock; another run started. Aborting.")
            return 1
        lock_fd.seek(0)
        lock_fd.truncate(0)
        lock_fd.write(f"pid={os.getpid()}\nstarted={__import__('datetime').datetime.now().isoformat()}\n")
        lock_fd.flush()

    # The pipeline loads the embedding model, so only build it when writing.
    pipeline = None
    if args.apply:
        from v4.pipeline import V4Pipeline
        pipeline = V4Pipeline(enable_llm=True, enable_embeddings=True, dry_run=False)
        cb = pipeline.storage
        cluster = cb.cluster
    else:
        from storage.couchbase_client import CouchbaseClient
        cb = CouchbaseClient()
        cluster = cb.cluster

    indexed = cb.collection.get(REPO_COMMITS_INDEX_DOC_ID).content_as[dict]["repos"]
    repos_root = Path(os.getenv("REPOS_PATH", os.path.expanduser("~/codesmriti-repos")))

    # Every shell path the corpus already holds, so we only add what is missing.
    have: Dict[str, set] = {}
    for row in cluster.query(
        "SELECT repo_id, file_path FROM `code_kosha` WHERE type = 'file_index'"
    ):
        have.setdefault(row["repo_id"], set()).add(row.get("file_path"))

    targets = [args.repo] if args.repo else sorted(indexed)
    work: Dict[str, List[Path]] = {}
    for repo_id in targets:
        repo_path = repos_root / repo_id.replace("/", "_")
        if not repo_path.is_dir():
            continue
        found = []
        for ext in SHELL_EXTENSIONS:
            for p in repo_path.rglob(f"*{ext}"):
                if should_skip_file(p):
                    continue
                rel = str(p.relative_to(repo_path))
                if rel not in have.get(repo_id, ()):
                    found.append(p)
        if found:
            work[repo_id] = sorted(found)

    total = sum(len(v) for v in work.values())
    print(f"{total} shell file(s) missing across {len(work)} repo(s)\n")
    for repo_id, files in sorted(work.items())[: args.limit]:
        print(f"  {repo_id:42s} {len(files)}")
    if args.limit and len(work) > args.limit:
        print(f"  ... and {len(work) - args.limit} more repo(s) not listed")

    if not args.apply:
        print("\nDry run — re-run with --apply to write.")
        return 0

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    written = failed = 0

    try:
        for repo_id, files in sorted(work.items())[: args.limit]:
            repo_path = repos_root / repo_id.replace("/", "_")
            commit = indexed.get(repo_id) or "unknown"
            docs = []
            for full_path in files:
                rel = str(full_path.relative_to(repo_path))
                try:
                    fi, symbols, units = loop.run_until_complete(
                        pipeline.file_processor.process(
                            file_path=full_path,
                            repo_path=repo_path,
                            repo_id=repo_id,
                            commit_hash=commit,
                            parent_module_id="",
                        )
                    )
                except Exception as e:
                    logger.error(f"  {repo_id} {rel}: {e}")
                    failed += 1
                    continue
                if not fi:
                    # Too small, or filtered downstream — not an error.
                    logger.info(f"  {repo_id} {rel}: produced no document")
                    continue
                docs.extend([fi, *symbols, *units])
                written += 1
                logger.info(f"  {repo_id} {rel}: +1 file, {len(symbols)} symbols, {len(units)} units")

            if not docs:
                continue
            if pipeline.embedding_generator:
                for d in docs:
                    text = getattr(d, "_embedding_text", None) or getattr(d, "content", "")
                    if text:
                        d.embedding = pipeline.embedding_generator.generate_embedding(text)
            for d in docs:
                cb.collection.upsert(d.document_id, d.to_dict())
            logger.info(f"{repo_id}: upserted {len(docs)} document(s)")

            if args.regenerate_summaries:
                from v4.incremental.updater import IncrementalUpdater
                u = IncrementalUpdater(dry_run=False, enable_llm=True)
                modules = u.get_affected_modules([str(p.relative_to(repo_path)) for p in files])
                u._regenerate_summaries(repo_id, commit, modules, loop)
    finally:
        loop.close()
        if lock_fd is not None:
            import fcntl
            from v4.incremental.runner import IngestionRunner
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
                IngestionRunner.LOCK_FILE.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Could not release the ingestion lock: {e}")

    print(f"\nindexed {written} file(s), {failed} failure(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
