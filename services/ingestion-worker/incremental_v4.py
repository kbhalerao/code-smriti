#!/usr/bin/env python3
"""
Incremental V4 Update CLI - Git-based change detection with surgical updates.

Compares origin HEAD to stored commit, processes only changed files.
Falls back to full re-ingestion if >threshold% of files changed.

Usage:
    python incremental_v4.py                    # All repos
    python incremental_v4.py --repo owner/name  # Single repo
    python incremental_v4.py --dry-run          # Preview changes (runs LLM, skips DB)
    python incremental_v4.py --threshold 0.10   # 10% threshold
    python incremental_v4.py --status           # Check if ingestion is running

Features:
    - File-based locking prevents overlapping runs
    - Rotating log files in logs/
    - Run history stored in Couchbase (ingestion_log documents)

Strategy:
    1. git fetch origin for each repo
    2. Compare origin/main HEAD to stored commit (in repo_summary)
    3. If same -> skip
    4. If different:
       - Get changed files via git diff
       - If >threshold% changed -> full re-ingest
       - Otherwise -> surgical update:
         a. Delete docs for deleted files
         b. Process only changed files (reusing V4Pipeline)
         c. Check significance of changes
         d. Regenerate affected module_summary and repo_summary (if significant)
    5. Run doc ingestion for changed .md/.rst/.txt files

Lifecycle:
    - Clones new repos from GitHub API or config file
    - Deletes orphaned repo docs from Couchbase
    - Skips repos with no changes
"""

import argparse
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from llm_enricher import LLM_CONFIG
from v4.incremental.runner import IngestionRunner, LockError, check_running
from v4.incremental.updater import DEFAULT_REINGEST_THRESHOLD


def main():
    parser = argparse.ArgumentParser(
        description="Incremental V4 Update - Git-based change detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        "--repo",
        type=str,
        help="Single repo to update (format: owner/name)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes (runs LLM summaries but skips DB writes)"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_REINGEST_THRESHOLD,
        # argparse %-expands help strings, so a literal percent must be doubled.
        help="Fraction of a repo's indexable files that must change before a "
             "full rebuild replaces a surgical update "
             f"(default: {DEFAULT_REINGEST_THRESHOLD} = {DEFAULT_REINGEST_THRESHOLD * 100:.0f}%%)"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM (basic summaries only)"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check if an ingestion is currently running"
    )
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Decide what needs doing and queue it, without doing any of it. "
             "The only half that talks to git. Safe to run while a drain is working."
    )
    parser.add_argument(
        "--drain",
        action="store_true",
        help="Work the durable queue under the ingestion lock. Does no git fetch: "
             "every item is pinned to the commit range the scan decided against."
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=None,
        help="Stop the drain after this many repos (default: until the queue is empty)"
    )
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Show what is waiting, longest-waiting first, then exit"
    )
    parser.add_argument(
        "--dlq",
        action="store_true",
        help="List files that failed and were not recovered, then exit. Nothing "
             "drains this queue automatically — an entry means the pipeline could "
             "not fix it by retrying and a human needs to look."
    )
    parser.add_argument(
        "--trigger",
        choices=["manual", "scheduled", "webhook"],
        default="manual",
        help="How this run was triggered (for logging)"
    )

    args = parser.parse_args()

    # Status check mode
    if args.status:
        running_info = check_running()
        if running_info:
            print(f"Ingestion is RUNNING")
            print(f"  PID: {running_info.get('pid', 'unknown')}")
            print(f"  Started: {running_info.get('started', 'unknown')}")
            sys.exit(0)
        else:
            print("No ingestion running")
            sys.exit(0)

    # Queue inspection. Read-only, takes no lock.
    if args.queue:
        from loguru import logger as _log
        _log.remove()
        from storage.couchbase_client import CouchbaseClient
        from v4.incremental.queue import IngestionQueue

        q = IngestionQueue(CouchbaseClient())
        counts = q.counts()
        if not counts:
            print("Queue is empty.")
            sys.exit(0)
        print("  ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        print()
        for item in q.pending(limit=100):
            plan = item.get("plan") or {}
            files = len(plan.get("code_to_process") or [])
            attempts = item.get("attempts", 0)
            print(
                f"  [{item.get('state'):6}] {item.get('repo_id'):45} "
                f"{plan.get('action', '?'):12} {files:4} file(s)"
                + (f"  attempts={attempts}" if attempts else "")
            )
        sys.exit(0)

    # Dead-letter inspection mode. Read-only, takes no lock, and deliberately
    # exits non-zero when there is something to look at so a caller can gate on it.
    if args.dlq:
        # Silence loguru first. This output is consumed by run_incremental.sh —
        # both as the body of the cos notice and as the input to the
        # change-fingerprint — and CouchbaseClient logs its connection banner to
        # stderr on construction, which run_with_timeout merges into the capture.
        from loguru import logger
        logger.remove()

        from storage.couchbase_client import CouchbaseClient
        from v4.dlq import DeadLetterQueue

        entries = DeadLetterQueue(CouchbaseClient()).open_entries(repo_id=args.repo)
        if not entries:
            print("Dead-letter queue is empty.")
            sys.exit(0)

        print(f"{len(entries)} open dead-letter entry/entries:\n")
        for e in entries:
            # The "  [kind] repo file" line is the STABLE IDENTITY of an entry and
            # is what the wrapper fingerprints to decide whether anything actually
            # changed. Nothing that moves on its own — count, timestamps, run id —
            # may appear on it, or a persistently broken file would re-alert every
            # single night and the notice would stop being read.
            print(f"  [{e.get('kind')}] {e.get('repo_id')} {e.get('file_path')}")
            seen = f", seen {e.get('count')}x" if e.get("count", 1) > 1 else ""
            print(f"      last {e.get('last_seen')} (run {e.get('run_id') or 'unknown'}{seen})")
            print(f"      {str(e.get('detail'))[:300]}")
        sys.exit(1)

    # LLM config is env-driven (LLM_BASE_URL / LLM_MODEL / LLM_PROVIDER)
    llm_config = LLM_CONFIG

    # Scan: plans and queues, never executes. Deliberately does NOT take the
    # ingestion lock — a scan and a drain are meant to run on the same tick.
    if args.scan:
        import fcntl
        from pathlib import Path as _Path
        from v4.incremental.updater import IncrementalUpdater

        lock_path = _Path(__file__).parent / "logs" / "scan.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = open(lock_path, "a+")
        try:
            # Its own lock, not the ingestion one. ~100 fetches does not sit
            # comfortably inside a 5-minute tick, so two scans can overlap
            # without this; two drains cannot, because of the ingestion lock.
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            print("Another scan is running; skipping.")
            sys.exit(0)

        updater = IncrementalUpdater(
            threshold=args.threshold, dry_run=args.dry_run,
            enable_llm=not args.no_llm, llm_config=llm_config,
        )
        stats = updater.scan(repo_filter=args.repo)
        print(" ".join(f"{k}={v}" for k, v in stats.items()))
        sys.exit(0)

    # A drain tick with an empty queue must be nearly free. Constructing the
    # runner builds V4Pipeline, which loads the embedding model — tens of seconds
    # of work to discover there is nothing to do, every five minutes, forever. So
    # ask the queue first, with nothing but a Couchbase client.
    if args.drain:
        from loguru import logger as _log
        from storage.couchbase_client import CouchbaseClient
        from v4.incremental.queue import IngestionQueue, STATE_QUEUED

        if not IngestionQueue(CouchbaseClient()).counts().get(STATE_QUEUED):
            print("Queue is empty; nothing to drain.")
            sys.exit(0)

    # Initialize runner (handles locking and logging)
    runner = IngestionRunner(
        threshold=args.threshold,
        dry_run=args.dry_run,
        enable_llm=not args.no_llm,
        llm_config=llm_config,
        trigger=args.trigger
    )

    try:
        if args.drain:
            results = runner.drain(max_items=args.max_items)
        else:
            results = runner.run(repo_filter=args.repo)

        # Exit with error if any failures
        if any(r.status == 'error' for r in results):
            sys.exit(1)

    except LockError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Use --status to check the running process", file=sys.stderr)
        sys.exit(2)

    except KeyboardInterrupt:
        print("\nInterrupted by user", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
