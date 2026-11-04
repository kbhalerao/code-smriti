#!/usr/bin/env python3
"""
Retry sweep for commit sizing.

The tick sizes commits as each repo is ingested, and retries failures only when
that repo is ingested again. This sweep covers the quiet repos: for every
allowlisted repo it re-checks the lookback window behind the commit the corpus
holds (`repo_commits_index`) and sizes anything still missing. It is run by the
daily report stage of run_incremental.sh.

It is NOT a backfill. History older than the lookback window is loaded from the
StrategicPlanning run, by insert, into the same collection.

Usage:
    uv run python scripts/size_commits.py              # sweep all allowlisted repos
    uv run python scripts/size_commits.py --repo owner/name
    uv run python scripts/size_commits.py --dry-run    # list candidates; no model calls, no writes
"""

import argparse
import sys
from pathlib import Path

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import WorkerConfig
from storage.couchbase_client import CouchbaseClient
from v4.incremental.commit_sizing import SizingStats, candidate_commits, sizer_from_config
from v4.incremental.repo_lifecycle import RepoLifecycle


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", help="only this owner/name")
    ap.add_argument("--dry-run", action="store_true", help="list unsized candidates; no model calls, no writes")
    args = ap.parse_args()

    config = WorkerConfig()
    cb = CouchbaseClient()
    sizer = sizer_from_config(cb, config)
    if sizer is None:
        logger.warning("Commit sizing is disabled or unavailable; nothing to do")
        return 0

    lifecycle = RepoLifecycle(repos_path=Path(config.repos_path), cb_client=cb,
                              github_token=config.github_token)
    tips = lifecycle.get_commits_index()
    repos = sorted(r for r in tips if sizer.wants(r) and (not args.repo or r == args.repo))
    logger.info(f"Sweeping {len(repos)} repo(s), lookback {sizer.lookback_days} days")

    total = SizingStats()
    for repo_id in repos:
        repo_path = lifecycle.repo_id_to_path(repo_id)
        if not repo_path.exists():
            continue
        if args.dry_run:
            try:
                shas = candidate_commits(repo_path, tips[repo_id], None, sizer.lookback_days)
            except Exception as e:
                logger.warning(f"{repo_id}: {e}")
                continue
            missing = [s for s in shas if not sizer.exists(repo_id, s)]
            if missing:
                logger.info(f"{repo_id}: {len(missing)} unsized of {len(shas)}")
            continue
        stats = sizer.size_repo(repo_id, repo_path, tips[repo_id])
        for k in ("sized", "excluded", "existing", "failed"):
            setattr(total, k, getattr(total, k) + getattr(stats, k))
        for k, v in stats.by_class.items():
            total.by_class[k] = total.by_class.get(k, 0) + v
        if sizer.unavailable:
            logger.warning("Sizing model unavailable; stopping sweep")
            break

    if not args.dry_run:
        logger.info(f"Sweep: {total}")
    # Fail-soft: an unreachable model is retried tomorrow, not an alert.
    return 0


if __name__ == "__main__":
    sys.exit(main())
