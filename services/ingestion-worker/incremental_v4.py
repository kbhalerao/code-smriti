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

from llm_enricher import LMSTUDIO_CONFIG, OLLAMA_CONFIG
from v4.incremental.runner import IngestionRunner, LockError, check_running


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
        default=0.05,
        help="Change threshold for full re-ingestion (default: 0.05 = 5%%)"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM (basic summaries only)"
    )
    parser.add_argument(
        "--llm-provider",
        choices=["lmstudio", "ollama"],
        default="lmstudio",
        help="LLM provider (default: lmstudio)"
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Check if an ingestion is currently running"
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

    # Select LLM config
    llm_config = LMSTUDIO_CONFIG if args.llm_provider == "lmstudio" else OLLAMA_CONFIG

    # Initialize runner (handles locking and logging)
    runner = IngestionRunner(
        threshold=args.threshold,
        dry_run=args.dry_run,
        enable_llm=not args.no_llm,
        llm_config=llm_config,
        trigger=args.trigger
    )

    try:
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
