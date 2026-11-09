#!/usr/bin/env python3
"""
Reprocess the files in the ingestion dead-letter queue.

An entry is cleared only when its file is reprocessed successfully, and
incremental ingestion reprocesses a file only when it changes. Without a replay,
a file that failed for a passing reason (contention, a one-off bad response, a
pipeline bug since fixed) would stay in the queue, and stay degraded in the
corpus, until someone happened to edit it.

`--auto` is the scheduled form, run from the daily report stage of
run_incremental.sh. It replays only entries that still have automatic attempts
left (`v4.dlq.MAX_REPLAY_ATTEMPTS`) and counts each try against them. An entry
that is still failing once they are used up is *exhausted*, and only exhausted
entries are alerted on. A manual run without `--auto` replays every entry and
does not use up attempts.

This script runs each open entry's file through the same `process_file_bounded`
call the ingestion paths use. It upserts the documents and drops any child
documents the new parse no longer produces, using `_reconcile_file_children`
exactly as the updater does. It then clears the entry if the file came through
cleanly, and records it again if it did not, so the queue stays honest either
way. The commits index is not moved: the repo is still indexed at the same
commit. Module and repo summaries are not regenerated; they pick the change up
on the repo's next real update.

Dry run unless --apply (or --auto). Writing takes the ingestion lock, as
`backfill_shell_files.py` does: this is GPU-heavy corpus mutation, which is what
the lock serialises.
"""
import argparse
import asyncio
import fcntl
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from loguru import logger


def _worktree_head(repo_path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="reprocess and write (default: dry run)")
    ap.add_argument("--repo", default=None, help="limit to one repo_id")
    ap.add_argument("--kind", default=None, choices=["chunker", "summary", "timeout", "exception"],
                    help="limit to one failure kind")
    ap.add_argument("--auto", action="store_true",
                    help="scheduled mode: implies --apply, replays only entries with attempts left, "
                         "counts each try, exits 2 if ingestion holds the lock")
    ap.add_argument("--force", action="store_true",
                    help="run even while a scheduled ingestion holds the lock")
    args = ap.parse_args()
    if args.auto:
        args.apply = True

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")

    from config import WorkerConfig
    from storage.couchbase_client import CouchbaseClient
    from v4.dlq import DeadLetterQueue, is_exhausted

    config = WorkerConfig()
    repos_root = Path(config.repos_path)

    entries = DeadLetterQueue(CouchbaseClient()).open_entries(repo_id=args.repo, limit=1000)
    if args.kind:
        entries = [e for e in entries if e.get("kind") == args.kind]
    if args.auto:
        entries = [e for e in entries if not is_exhausted(e)]

    # One file can carry several entries (say, a chunker failure and a timeout).
    # It is reprocessed once and every entry it has is resolved together.
    files: dict = {}
    for e in entries:
        files.setdefault((e["repo_id"], e["file_path"]), []).append(e)

    print(f"{len(entries)} open entr(ies) over {len(files)} file(s)\n")
    for (repo_id, rel), es in sorted(files.items()):
        kinds = ",".join(sorted(e["kind"] for e in es))
        exists = (repos_root / repo_id.replace("/", "_") / rel).is_file()
        print(f"  [{kinds:16s}] {repo_id} {rel}{'' if exists else '   (file no longer exists)'}")

    if not args.apply:
        print("\nDry run — re-run with --apply to reprocess.")
        return 0
    if not files:
        return 0

    lock_fd = None
    if not args.force:
        from v4.incremental.runner import IngestionRunner, check_running
        running = check_running()
        if running:
            print(f"An ingestion is running (pid {running.get('pid')}, started "
                  f"{running.get('started')}). Re-run when it finishes, or --force.")
            # 2 is the wrapper's "clean skip": tomorrow's run tries again.
            return 2
        lock_fd = open(IngestionRunner.LOCK_FILE, "a+")
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_fd.close()
            print("Could not take the ingestion lock; another run started. Aborting.")
            return 2
        lock_fd.seek(0)
        lock_fd.truncate(0)
        lock_fd.write(f"pid={os.getpid()}\nstarted={datetime.now().isoformat()}\n")
        lock_fd.flush()

    # The updater owns the pipeline (and so the embedding model), the DLQ and the
    # child reconciliation, so one instance gives this script the same write path
    # an incremental run takes.
    from v4.incremental.updater import IncrementalUpdater
    updater = IncrementalUpdater(dry_run=False, enable_llm=True)
    updater.run_id = f"replay_dlq_{datetime.now():%Y%m%d_%H%M%S}"
    pipeline, cb, dlq = updater.pipeline, updater.cb_client, updater.dlq

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    recovered = still_failing = gone = 0

    try:
        for (repo_id, rel), es in sorted(files.items()):
            repo_path = repos_root / repo_id.replace("/", "_")
            full_path = repo_path / rel
            if not full_path.is_file():
                # Deleted since it failed. The updater removes a deleted file's
                # documents, so there is nothing left in the corpus to repair.
                dlq.clear(repo_id, [rel])
                gone += 1
                logger.info(f"{repo_id} {rel}: file no longer exists; entry cleared")
                continue

            # Process the file as it is on disk, labelled with the commit it
            # failed at. If the worktree has moved since, say so: the content may
            # be newer than the label.
            commit = es[0].get("commit") or ""
            head = _worktree_head(repo_path)
            if commit and head and head != commit:
                logger.warning(f"{repo_id} {rel}: worktree at {head[:10]}, entry at {commit[:10]}")

            result = loop.run_until_complete(pipeline.process_file_bounded(
                file_path=full_path, repo_path=repo_path, repo_id=repo_id,
                commit_hash=commit or head,
            ))

            docs = [d for d in (result.file_index, *result.symbols, *result.semantic_units) if d]
            if docs:
                if pipeline.embedding_generator:
                    for d in docs:
                        text = getattr(d, "_embedding_text", None) or getattr(d, "content", "")
                        if text:
                            d.embedding = pipeline.embedding_generator.generate_embedding(text)
                for d in docs:
                    cb.collection.upsert(d.document_id, d.to_dict())
                # Only after the new generation is written, as the updater does.
                updater._reconcile_file_children(
                    repo_id, rel, [d.document_id for d in (*result.symbols, *result.semantic_units)])

            if result.failed:
                # Drop only the kinds that did NOT recur, so a recurring entry
                # keeps its history (first_seen, count) and a kind that has
                # cleared stops being reported.
                new_kinds = {f.kind.value for f in result.failures}
                dlq.clear(repo_id, [rel], kinds=[e["kind"] for e in es if e["kind"] not in new_kinds])
                for f in result.failures:
                    f.retried = True
                # Only a scheduled replay uses up an attempt; a person re-running
                # by hand should not exhaust an entry's automatic tries.
                prev = max(e.get("replay_attempts", 0) for e in es)
                dlq.record(repo_id, result.failures, run_id=updater.run_id, commit=commit,
                           replay_attempts=prev + 1 if args.auto else prev)
                still_failing += 1
                logger.error(f"{repo_id} {rel}: still failing — "
                             + "; ".join(f"{f.kind.value}: {f.detail}" for f in result.failures))
            else:
                dlq.clear(repo_id, [rel])
                recovered += 1
                logger.info(f"{repo_id} {rel}: recovered ({len(docs)} document(s))")
    finally:
        loop.close()
        if lock_fd is not None:
            from v4.incremental.runner import IngestionRunner
            try:
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
                IngestionRunner.LOCK_FILE.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Could not release the ingestion lock: {e}")

    print(f"\nrecovered {recovered}, still failing {still_failing}, file gone {gone}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
