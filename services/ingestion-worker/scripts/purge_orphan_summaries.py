"""
Purge orphan module_summary and repo_summary documents.

Background:
    module_summary and repo_summary docs are keyed by commit_hash and are fully
    regenerated on every incremental run (under the new origin_head). The
    incremental cleanup path (updater._delete_old_file_docs) only ever purged
    file_index / symbol_index docs, so each prior commit's summaries were left
    behind. Over many daily runs these accumulate in the bucket AND the FTS
    index, polluting retrieval with stale snapshots (e.g. one repo held 14k
    module summaries across 31 commits when only ~530 were current).

Definition of "current":
    A repo's current commit = the commit_hash of its most-recently-created
    repo_summary (MAX version.created_at). module_summary and repo_summary docs
    from the same incremental run share that commit_hash, so this single anchor
    identifies the live set for both types.

Safety:
    - Dry-run by default; pass --execute to delete.
    - Repos with no repo_summary are SKIPPED (cannot anchor a current commit),
      never blind-deleted.
    - Deletes are scoped per (repo, type, commit != current).

Usage:
    ./.venv/bin/python scripts/purge_orphan_summaries.py            # dry-run
    ./.venv/bin/python scripts/purge_orphan_summaries.py --execute  # delete
"""

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from couchbase.options import QueryOptions
from loguru import logger

from storage.couchbase_client import CouchbaseClient

BUCKET = "code_kosha"
SUMMARY_TYPES = ("module_summary", "repo_summary")


def current_commit_by_repo(cb: CouchbaseClient) -> dict[str, str]:
    """Map repo_id -> commit_hash of its newest repo_summary (by created_at)."""
    query = f"""
        SELECT repo_id, commit_hash, version.created_at AS created_at
        FROM `{BUCKET}`
        WHERE type = 'repo_summary'
    """
    newest: dict[str, tuple[str, str]] = {}  # repo_id -> (created_at, commit_hash)
    for row in cb.cluster.query(query):
        repo = row.get("repo_id")
        commit = row.get("commit_hash")
        created = row.get("created_at") or ""
        if not repo or not commit:
            continue
        if repo not in newest or created > newest[repo][0]:
            newest[repo] = (created, commit)
    return {repo: commit for repo, (_, commit) in newest.items()}


def summary_counts(cb: CouchbaseClient) -> dict[str, dict[str, dict[str, int]]]:
    """Per doc-type: {repo_id: {commit_hash: count}}."""
    counts: dict[str, dict[str, dict[str, int]]] = {
        t: defaultdict(lambda: defaultdict(int)) for t in SUMMARY_TYPES
    }
    for doc_type in SUMMARY_TYPES:
        query = f"""
            SELECT repo_id, commit_hash, COUNT(*) AS n
            FROM `{BUCKET}`
            WHERE type = '{doc_type}'
            GROUP BY repo_id, commit_hash
        """
        for row in cb.cluster.query(query):
            repo = row.get("repo_id")
            commit = row.get("commit_hash")
            if repo and commit:
                counts[doc_type][repo][commit] = row.get("n", 0)
    return counts


def delete_orphans_for_repo(
    cb: CouchbaseClient, doc_type: str, repo_id: str, current_commit: str
) -> None:
    """Delete summaries for a repo whose commit_hash != current_commit."""
    query = f"""
        DELETE FROM `{BUCKET}`
        WHERE type = $doc_type
          AND repo_id = $repo_id
          AND commit_hash != $current_commit
    """
    result = cb.cluster.query(
        query,
        QueryOptions(named_parameters={
            "doc_type": doc_type,
            "repo_id": repo_id,
            "current_commit": current_commit,
        }),
    )
    _ = list(result)  # consume to force execution


def main() -> int:
    parser = argparse.ArgumentParser(description="Purge orphan summary docs")
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually delete (default is dry-run)",
    )
    args = parser.parse_args()

    cb = CouchbaseClient()

    logger.info("Resolving current commit per repo (newest repo_summary)...")
    current = current_commit_by_repo(cb)
    logger.info(f"Anchored current commit for {len(current)} repos")

    logger.info("Counting summaries by repo/commit...")
    counts = summary_counts(cb)

    grand_keep = grand_orphan = 0
    skipped_repos: set[str] = set()
    per_type_totals: dict[str, tuple[int, int]] = {}

    for doc_type in SUMMARY_TYPES:
        keep = orphan = 0
        offenders: list[tuple[str, int]] = []  # (repo, orphan_count)
        for repo, by_commit in counts[doc_type].items():
            total = sum(by_commit.values())
            cur = current.get(repo)
            if cur is None:
                skipped_repos.add(repo)
                keep += total  # untouched
                continue
            repo_keep = by_commit.get(cur, 0)
            repo_orphan = total - repo_keep
            keep += repo_keep
            orphan += repo_orphan
            if repo_orphan:
                offenders.append((repo, repo_orphan))

        per_type_totals[doc_type] = (keep, orphan)
        grand_keep += keep
        grand_orphan += orphan

        offenders.sort(key=lambda x: -x[1])
        logger.info(f"\n=== {doc_type} ===")
        logger.info(f"  keep (current): {keep:,}   orphan (to delete): {orphan:,}")
        for repo, n in offenders[:8]:
            logger.info(f"    {repo}: {n:,} orphans")

    logger.info(f"\n{'='*60}")
    logger.info(f"TOTAL keep: {grand_keep:,}   TOTAL orphans: {grand_orphan:,}")
    if skipped_repos:
        logger.warning(
            f"SKIPPED {len(skipped_repos)} repo(s) with no repo_summary anchor "
            f"(left untouched): {sorted(skipped_repos)[:10]}"
        )

    if not args.execute:
        logger.info("\nDRY RUN — no changes made. Re-run with --execute to delete.")
        return 0

    logger.info("\nExecuting deletes...")
    for doc_type in SUMMARY_TYPES:
        deleted_repos = 0
        for repo, by_commit in counts[doc_type].items():
            cur = current.get(repo)
            if cur is None:
                continue
            if sum(by_commit.values()) - by_commit.get(cur, 0) == 0:
                continue  # nothing to delete
            delete_orphans_for_repo(cb, doc_type, repo, cur)
            deleted_repos += 1
        logger.info(f"  {doc_type}: purged orphans across {deleted_repos} repos")

    logger.info("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
