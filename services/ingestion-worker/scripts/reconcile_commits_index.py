#!/usr/bin/env python3
"""
Reconcile the repo commits index against what the corpus actually contains.

`repo_commits_index` is what incremental ingestion reads to decide whether a
repo has changed. It is written once per run, from a results list that only
exists after the whole repo loop returns — so a run killed part-way (which is
how the watchdog ends a wedged run) banks nothing, and every repo it finished
gets re-ingested next time. On 2026-08-21 that discarded six fully ingested
repos and would have made the backlog undrainable.

Each repo's `repo_summary` document, by contrast, is written as that repo
finishes and carries the commit it was ingested at. So the corpus already knows
what the index forgot. This script moves the index forward to match.

The two records read *different refs*, and the difference matters. The index
stores `origin/HEAD` (what the next run compares against); repo_summary stores
`git rev-parse HEAD` — the worktree the pipeline actually read. They agree only
while the worktree is anchored to origin/HEAD. Where they disagree the corpus
follows repo_summary, because that is the content that was parsed, but a
disagreement is also a sign that the clone was on another branch, so prefer
--only over a blanket run and check what the extra entries mean first.

It only ever moves an entry FORWARD, and only on proof:

  * the indexed commit is an ancestor of the corpus commit (an ordinary
    fast-forward), or
  * the corpus commit is the clone's current origin/HEAD (true after a branch
    re-anchor, where ancestry legitimately does not hold)

and only when the repo actually has file_index documents. Anything else is
reported and left alone. Dry run unless --apply.

Note that v4/incremental/updater.py now checkpoints each repo as it completes,
so this should not be needed again; it remains the way to verify that invariant.
"""
import argparse
import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions

from v4.incremental.repo_lifecycle import REPO_COMMITS_INDEX_DOC_ID


def git(repo_path, *args):
    """Run git in repo_path, returning stripped stdout or None."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.SubprocessError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def corpus_is_ahead(repo_path, indexed, corpus):
    """
    Is `corpus` a commit the index should be moved forward to?

    Returns (True, reason) or (False, reason).
    """
    if not repo_path.exists():
        return False, "clone missing"

    head = git(repo_path, "rev-parse", "origin/HEAD")
    if corpus == head:
        return True, "corpus commit is current origin/HEAD"

    if indexed and git(repo_path, "merge-base", "--is-ancestor", indexed, corpus) is not None:
        return True, "indexed commit is an ancestor of the corpus commit"

    return False, "corpus commit is neither origin/HEAD nor a descendant of the index"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the index (default is a dry run)")
    ap.add_argument("--repos-path", default=None, help="override REPOS_PATH")
    ap.add_argument("--only", default=None,
                    help="comma-separated repo_ids to consider; everything else is left alone")
    args = ap.parse_args()

    load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")
    repos_root = Path(args.repos_path or os.getenv("REPOS_PATH", os.path.expanduser("~/codesmriti-repos")))

    cluster = Cluster(
        f"couchbase://{os.getenv('COUCHBASE_HOST', 'localhost')}",
        ClusterOptions(PasswordAuthenticator("Administrator", os.environ["COUCHBASE_PASSWORD"])),
    )
    cluster.wait_until_ready(timedelta(seconds=20))
    coll = cluster.bucket("code_kosha").default_collection()

    index_doc = coll.get(REPO_COMMITS_INDEX_DOC_ID).content_as[dict]
    indexed = index_doc.get("repos", {})
    print(f"commits index: {len(indexed)} repos, updated_at={index_doc.get('updated_at')}")

    corpus = {
        r["repo_id"]: r["commit_hash"]
        for r in cluster.query(
            "SELECT repo_id, commit_hash FROM `code_kosha` "
            "WHERE type = 'repo_summary' AND commit_hash IS NOT MISSING"
        )
    }
    file_counts = {
        r["repo_id"]: r["n"]
        for r in cluster.query(
            "SELECT repo_id, COUNT(*) n FROM `code_kosha` WHERE type = 'file_index' GROUP BY repo_id"
        )
    }
    print(f"corpus:        {len(corpus)} repo_summary docs\n")

    only = {r.strip() for r in args.only.split(",")} if args.only else None
    if only:
        missing = only - corpus.keys()
        if missing:
            print(f"  note: no repo_summary for {', '.join(sorted(missing))}\n")

    updates, anomalies, out_of_scope = {}, [], []
    for repo_id, corpus_commit in sorted(corpus.items()):
        index_commit = indexed.get(repo_id)
        if index_commit == corpus_commit:
            continue
        if only is not None and repo_id not in only:
            out_of_scope.append((repo_id, index_commit, corpus_commit))
            continue

        files = file_counts.get(repo_id, 0)
        if files == 0:
            anomalies.append((repo_id, index_commit, corpus_commit, "no file_index documents"))
            continue

        repo_path = repos_root / repo_id.replace("/", "_")
        ok, reason = corpus_is_ahead(repo_path, index_commit, corpus_commit)
        if ok:
            updates[repo_id] = corpus_commit
            print(f"  BANK  {repo_id:40s} {str(index_commit)[:8]} -> {corpus_commit[:8]}  "
                  f"({files} files; {reason})")
        else:
            anomalies.append((repo_id, index_commit, corpus_commit, reason))

    if out_of_scope:
        print(f"\n  {len(out_of_scope)} repo(s) also drifted but outside --only:")
        for repo_id, i, c in out_of_scope:
            print(f"  OUT   {repo_id:40s} {str(i)[:8]} vs {str(c)[:8]}")

    if anomalies:
        print(f"\n  {len(anomalies)} repo(s) left alone:")
        for repo_id, i, c, why in anomalies:
            print(f"  SKIP  {repo_id:40s} {str(i)[:8]} vs {str(c)[:8]}  ({why})")

    if not updates:
        print("\nNothing to reconcile.")
        return 0

    print(f"\n{len(updates)} repo(s) to bank.")
    if not args.apply:
        print("Dry run — re-run with --apply to write.")
        return 0

    index_doc["repos"].update(updates)
    index_doc["updated_at"] = __import__("datetime").datetime.now().isoformat()
    coll.upsert(REPO_COMMITS_INDEX_DOC_ID, index_doc)
    print("Commits index updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
