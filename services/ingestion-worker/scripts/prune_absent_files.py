#!/usr/bin/env python3
"""
Remove documents for files that no longer exist in their repository.

The incremental path already deletes a file's documents when git reports it
deleted or renamed (`repo_lifecycle.delete_file_docs`). That only covers
deletions inside a commit range it actually processed. A force-push, a re-clone,
or a branch change removes files without ever appearing as a `D` in a diff the
updater sees, and those documents then persist indefinitely.

Measured 2026-08-19: 207 of 32,443 `file_index` documents point at files absent
from disk, concentrated in a handful of repositories. It is 0.6% and harmless in
isolation, but nothing removes it, so it only grows.

Safety, and the guard that matters:
    A missing file and a missing clone look identical from a document's point of
    view, and the second must never be treated as the first — a repository that
    failed to clone would otherwise have its entire index deleted. So a
    repository is skipped entirely unless its directory exists and contains a
    plausible number of files; only within a healthy clone is an absent file
    taken as a real deletion.

    Beyond that: dry-run by default, per-repository reporting so an unexpected
    volume is visible before anything is written, and a cap on how much of one
    repository may be pruned in a single run.

Deletes the `file_index` and its `symbol_index` / `semantic_unit` children, which
is what the incremental path does for a detected deletion. Module and repository
summaries are not recomputed; they will be stale for the affected folders until
the next aggregation.

Usage:
    ./.venv/bin/python scripts/prune_absent_files.py
    ./.venv/bin/python scripts/prune_absent_files.py --execute
    ./.venv/bin/python scripts/prune_absent_files.py --execute --repo owner/name
"""

import argparse
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from couchbase.options import QueryOptions
from loguru import logger

from config import WorkerConfig
from storage.couchbase_client import CouchbaseClient

BUCKET = "code_kosha"
CHILD_TYPES = ("symbol_index", "semantic_unit")

# A clone holding fewer files than this is treated as broken rather than emptied.
MIN_FILES_FOR_HEALTHY_CLONE = 5

# Refuse to prune more than this share of one repository's files in a single run.
# A legitimate cleanup is a handful of paths; a third of a repository disappearing
# means the clone moved, not that the files did.
MAX_PRUNE_FRACTION = 0.25


def repo_dir(repos: Path, repo_id: str) -> Path:
    return repos / (repo_id or "").replace("/", "_")


def clone_is_healthy(path: Path) -> bool:
    if not path.is_dir():
        return False
    seen = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            seen += 1
            if seen >= MIN_FILES_FOR_HEALTHY_CLONE:
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true", help="delete; default is a dry run")
    ap.add_argument("--repo", help="restrict to one repo_id")
    ap.add_argument(
        "--allow-fraction", type=float, default=MAX_PRUNE_FRACTION,
        help=(f"raise the per-repository prune cap (default {MAX_PRUNE_FRACTION:.2f}). "
              "Use only after checking why so much of a repository is missing — a "
              "directory rename and a moved clone look identical from here."),
    )
    args = ap.parse_args()

    cb = CouchbaseClient()
    repos_path = Path(WorkerConfig().repos_path)

    where = "f.type = 'file_index'"
    params: Dict[str, object] = {}
    if args.repo:
        where += " AND f.repo_id = $r"
        params["r"] = args.repo
    rows = list(cb.cluster.query(
        f"SELECT f.repo_id, f.file_path FROM `{BUCKET}` f WHERE {where}",
        QueryOptions(named_parameters=params, timeout=timedelta(minutes=10)),
    ))

    by_repo: Dict[str, List[str]] = defaultdict(list)
    for row in rows:
        if row.get("repo_id") and row.get("file_path"):
            by_repo[row["repo_id"]].append(row["file_path"])

    stats = Counter()
    plan: Dict[str, List[str]] = {}

    for repo_id, paths in sorted(by_repo.items()):
        root = repo_dir(repos_path, repo_id)
        if not clone_is_healthy(root):
            logger.warning(f"  {repo_id}: clone missing or near-empty — skipped entirely")
            stats["repos_skipped_unhealthy"] += 1
            continue
        absent = [p for p in paths if not (root / p).is_file()]
        if not absent:
            continue
        share = len(absent) / len(paths)
        if share > args.allow_fraction:
            logger.warning(
                f"  {repo_id}: {len(absent)}/{len(paths)} files absent ({share:.0%}) — "
                f"over the {args.allow_fraction:.0%} cap, skipped. Check whether the "
                f"clone moved or a directory was renamed, then re-run with --allow-fraction."
            )
            stats["repos_skipped_over_cap"] += 1
            continue
        plan[repo_id] = absent
        stats["files_to_prune"] += len(absent)
        stats["repos_affected"] += 1
        logger.info(f"  {repo_id}: {len(absent)} of {len(paths)} absent")

    logger.info(
        f"{'PRUNING' if args.execute else 'DRY RUN over'} "
        f"{stats['files_to_prune']} files across {stats['repos_affected']} repositories"
    )

    if args.execute:
        for repo_id, absent in plan.items():
            for file_path in absent:
                try:
                    result = cb.cluster.query(
                        f"""
                        DELETE FROM `{BUCKET}`
                        WHERE repo_id = $r AND file_path = $f
                          AND type IN ['file_index', 'symbol_index', 'semantic_unit']
                        """,
                        QueryOptions(named_parameters={"r": repo_id, "f": file_path},
                                     timeout=timedelta(minutes=2)),
                    )
                    # An unconsumed N1QL result never executes the mutation.
                    _ = list(result)
                    stats["files_pruned"] += 1
                    # Metrics are best-effort: metadata().metrics() returns None on
                    # this server version, and treating that as a failure reported
                    # 146 successful deletes as errors.
                    try:
                        metrics = result.metadata().metrics()
                        if metrics is not None:
                            stats["documents_deleted"] += metrics.mutation_count() or 0
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"  {repo_id}:{file_path} delete failed: {e}")
                    stats["delete_failed"] += 1

    logger.info("=" * 56)
    for key in sorted(stats):
        logger.info(f"  {key:<28} {stats[key]:>7,}")
    if not args.execute:
        logger.info("Dry run — nothing deleted. Re-run with --execute to apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
