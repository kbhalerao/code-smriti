#!/usr/bin/env python3
"""
Read back commit sizes from code_kosha.billing.commit_size.

Terminal only, by design: the data names client repositories, so it is not
rendered onto any page the gateway serves.

Usage:
    uv run python scripts/commit_size_report.py                  # by repo, last 30 days
    uv run python scripts/commit_size_report.py --by month --since 2026-01-01
    uv run python scripts/commit_size_report.py --by author
    uv run python scripts/commit_size_report.py --repo owner/name --list
    uv run python scripts/commit_size_report.py --csv sizes.csv  # every row, for a spreadsheet
"""

import argparse
import csv
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions, QueryOptions

from v4.incremental.commit_sizing import CLASSES, COLLECTION, SCOPE, SIZE_EXCLUDED

load_dotenv(Path(__file__).parent.parent / ".env")


def fetch(since: str, repo: str):
    cluster = Cluster(
        f"couchbase://{os.getenv('COUCHBASE_HOST', 'localhost')}",
        ClusterOptions(PasswordAuthenticator(
            os.getenv("COUCHBASE_USERNAME", "Administrator"), os.environ["COUCHBASE_PASSWORD"])),
    )
    cluster.wait_until_ready(timedelta(seconds=10))
    where = ["d.commit_date >= $since"]
    params = {"since": since}
    if repo:
        where.append("d.repo_id = $repo")
        params["repo"] = repo
    q = (f"SELECT d.repo_id, d.commit_hash, d.commit_date, d.author, d.`size`, d.p, "
         f"d.ai.assisted AS ai, d.truncated FROM `code_kosha`.`{SCOPE}`.`{COLLECTION}` d "
         f"WHERE {' AND '.join(where)} ORDER BY d.commit_date")
    return list(cluster.query(q, QueryOptions(named_parameters=params)))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default=(date.today() - timedelta(days=30)).isoformat())
    ap.add_argument("--repo", default="")
    ap.add_argument("--by", choices=["repo", "month", "author"], default="repo")
    ap.add_argument("--list", action="store_true", help="one line per commit")
    ap.add_argument("--csv", metavar="PATH", help="write every row to a CSV file")
    args = ap.parse_args()

    rows = fetch(args.since, args.repo)
    if not rows:
        print(f"No sized commits since {args.since}.")
        return 0

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["repo_id", "commit_hash", "commit_date", "author", "size",
                        "p_S", "p_M", "p_L", "ai_assisted", "truncated"])
            for r in rows:
                p = r.get("p") or {}
                w.writerow([r["repo_id"], r["commit_hash"], r["commit_date"], r.get("author"),
                            r["size"], p.get("S"), p.get("M"), p.get("L"),
                            r.get("ai"), r.get("truncated")])
        print(f"Wrote {len(rows)} rows to {args.csv}")
        return 0

    if args.list:
        for r in rows:
            p = r.get("p") or {}
            probs = " ".join(f"{k}{p[k]:.2f}" for k in CLASSES if k in p)
            print(f"{r['commit_date'][:10]}  {r['repo_id']:<40} {r['commit_hash'][:8]}  "
                  f"{r['size']:<8} {probs:<20} {'ai' if r.get('ai') else ''}")
        return 0

    key = {"repo": lambda r: r["repo_id"],
           "month": lambda r: r["commit_date"][:7],
           "author": lambda r: r.get("author") or "?"}[args.by]
    groups = defaultdict(lambda: defaultdict(int))
    for r in rows:
        g = groups[key(r)]
        g[r["size"]] += 1
        g["ai"] += bool(r.get("ai"))
        g["total"] += 1

    cols = list(CLASSES) + [SIZE_EXCLUDED]
    width = max(len(k) for k in groups) + 2
    print(f"Since {args.since}: {len(rows)} commits\n")
    print(f"{args.by:<{width}}" + "".join(f"{c:>9}" for c in cols) + f"{'total':>8}{'AI %':>7}")
    totals = defaultdict(int)
    for k in sorted(groups):
        g = groups[k]
        for c in cols + ["ai", "total"]:
            totals[c] += g[c]
        print(f"{k:<{width}}" + "".join(f"{g[c]:>9}" for c in cols)
              + f"{g['total']:>8}{g['ai'] / g['total']:>7.0%}")
    print(f"{'TOTAL':<{width}}" + "".join(f"{totals[c]:>9}" for c in cols)
          + f"{totals['total']:>8}{totals['ai'] / totals['total']:>7.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
