#!/usr/bin/env python3
"""
Velocity Analysis - Analyze commit velocity and LOC trends.

Queries commit_index documents to compute:
- Commits per week/month
- Lines of code added/deleted over time
- Acceleration metrics (is velocity increasing?)

Usage:
    python velocity_analysis.py                    # All repos, last 12 months
    python velocity_analysis.py --repo owner/name  # Single repo
    python velocity_analysis.py --months 6         # Last 6 months
    python velocity_analysis.py --weekly           # Weekly instead of monthly
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.couchbase_client import CouchbaseClient
from config import WorkerConfig

config = WorkerConfig()


def get_velocity_data(
    cb_client: CouchbaseClient,
    repo_filter: Optional[str] = None,
    months: int = 12,
    weekly: bool = False
):
    """
    Query commits and aggregate by time period.

    Returns list of dicts: [{period, commits, lines_added, lines_deleted, repos}]
    """
    # Calculate cutoff date
    cutoff = (datetime.now() - timedelta(days=months * 30)).isoformat()

    # Build query
    repo_clause = f"AND repo_id = '{repo_filter}'" if repo_filter else ""

    # Group by week or month
    if weekly:
        date_trunc = "DATE_TRUNC_STR(commit_date, 'week')"
        period_name = "week"
    else:
        date_trunc = "SUBSTR(commit_date, 0, 7)"  # YYYY-MM
        period_name = "month"

    query = f"""
        SELECT
            {date_trunc} as period,
            COUNT(*) as commits,
            SUM(metadata.lines_added) as lines_added,
            SUM(metadata.lines_deleted) as lines_deleted,
            COUNT(DISTINCT repo_id) as repos
        FROM `{config.couchbase_bucket}`
        WHERE type = 'commit_index'
          AND commit_date >= $cutoff
          {repo_clause}
        GROUP BY {date_trunc}
        ORDER BY period ASC
    """

    try:
        result = cb_client.cluster.query(query, cutoff=cutoff)
        data = list(result)
        return data, period_name
    except Exception as e:
        logger.error(f"Query failed: {e}")
        return [], period_name


def compute_acceleration(data: list) -> dict:
    """
    Compute acceleration metrics.

    Returns:
        - trend: "accelerating", "steady", or "decelerating"
        - avg_first_half vs avg_second_half
        - growth_rate
    """
    if len(data) < 4:
        return {"trend": "insufficient_data", "periods": len(data)}

    mid = len(data) // 2
    first_half = data[:mid]
    second_half = data[mid:]

    # Commits per period
    avg_commits_first = sum(d['commits'] for d in first_half) / len(first_half)
    avg_commits_second = sum(d['commits'] for d in second_half) / len(second_half)

    # LOC per period
    avg_loc_first = sum(d['lines_added'] or 0 for d in first_half) / len(first_half)
    avg_loc_second = sum(d['lines_added'] or 0 for d in second_half) / len(second_half)

    # Growth rates
    commit_growth = (avg_commits_second - avg_commits_first) / avg_commits_first if avg_commits_first > 0 else 0
    loc_growth = (avg_loc_second - avg_loc_first) / avg_loc_first if avg_loc_first > 0 else 0

    # Determine trend
    if commit_growth > 0.1:
        trend = "accelerating"
    elif commit_growth < -0.1:
        trend = "decelerating"
    else:
        trend = "steady"

    return {
        "trend": trend,
        "periods_analyzed": len(data),
        "first_half_avg_commits": round(avg_commits_first, 1),
        "second_half_avg_commits": round(avg_commits_second, 1),
        "commit_growth_rate": round(commit_growth * 100, 1),  # percentage
        "first_half_avg_loc": round(avg_loc_first, 0),
        "second_half_avg_loc": round(avg_loc_second, 0),
        "loc_growth_rate": round(loc_growth * 100, 1),
    }


def print_velocity_report(data: list, period_name: str, acceleration: dict, repo_filter: Optional[str]):
    """Print formatted velocity report."""
    print("\n" + "=" * 70)
    print("VELOCITY ANALYSIS REPORT")
    print("=" * 70)

    if repo_filter:
        print(f"Repository: {repo_filter}")
    else:
        print("Scope: All indexed repositories")

    print(f"Periods: {len(data)} {period_name}s")
    print()

    # Summary stats
    total_commits = sum(d['commits'] for d in data)
    total_added = sum(d['lines_added'] or 0 for d in data)
    total_deleted = sum(d['lines_deleted'] or 0 for d in data)

    print(f"Total commits: {total_commits:,}")
    print(f"Total lines added: {total_added:,}")
    print(f"Total lines deleted: {total_deleted:,}")
    print(f"Net LOC: {total_added - total_deleted:,}")
    print()

    # Acceleration analysis
    print("-" * 70)
    print("ACCELERATION ANALYSIS")
    print("-" * 70)

    if acceleration['trend'] == 'insufficient_data':
        print(f"Insufficient data ({acceleration['periods']} periods, need 4+)")
    else:
        trend_emoji = {
            "accelerating": "📈",
            "steady": "➡️",
            "decelerating": "📉"
        }
        print(f"Trend: {trend_emoji.get(acceleration['trend'], '')} {acceleration['trend'].upper()}")
        print()
        print(f"Commits/period (first half):  {acceleration['first_half_avg_commits']}")
        print(f"Commits/period (second half): {acceleration['second_half_avg_commits']}")
        print(f"Commit growth rate: {acceleration['commit_growth_rate']:+.1f}%")
        print()
        print(f"LOC/period (first half):  {acceleration['first_half_avg_loc']:,.0f}")
        print(f"LOC/period (second half): {acceleration['second_half_avg_loc']:,.0f}")
        print(f"LOC growth rate: {acceleration['loc_growth_rate']:+.1f}%")

    print()

    # Time series (last 12 periods or all if less)
    display_data = data[-12:] if len(data) > 12 else data

    print("-" * 70)
    print(f"TIME SERIES (last {len(display_data)} {period_name}s)")
    print("-" * 70)
    print(f"{'Period':<12} {'Commits':>10} {'Added':>12} {'Deleted':>12} {'Net':>12}")
    print("-" * 70)

    for d in display_data:
        period = d['period'][:10] if d['period'] else 'unknown'
        commits = d['commits']
        added = d['lines_added'] or 0
        deleted = d['lines_deleted'] or 0
        net = added - deleted
        print(f"{period:<12} {commits:>10,} {added:>12,} {deleted:>12,} {net:>+12,}")

    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="Analyze commit velocity and LOC trends",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument("--repo", type=str, help="Filter by repo (format: owner/name)")
    parser.add_argument("--months", type=int, default=12, help="Lookback period in months")
    parser.add_argument("--weekly", action="store_true", help="Aggregate weekly instead of monthly")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    # Configure logging
    logger.remove()
    if not args.json:
        logger.add(sys.stderr, format="<level>{message}</level>", level="INFO")

    # Connect
    cb_client = CouchbaseClient()

    # Query data
    data, period_name = get_velocity_data(
        cb_client,
        repo_filter=args.repo,
        months=args.months,
        weekly=args.weekly
    )

    if not data:
        print("No commit data found. Run backfill_commits.py first.")
        sys.exit(1)

    # Compute acceleration
    acceleration = compute_acceleration(data)

    # Output
    if args.json:
        import json
        output = {
            "repo_filter": args.repo,
            "period_type": period_name,
            "periods": data,
            "acceleration": acceleration,
        }
        print(json.dumps(output, indent=2, default=str))
    else:
        print_velocity_report(data, period_name, acceleration, args.repo)


if __name__ == "__main__":
    main()
