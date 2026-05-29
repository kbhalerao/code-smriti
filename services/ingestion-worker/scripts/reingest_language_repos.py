#!/usr/bin/env python3
"""
Re-ingest repos whose Swift / Elixir / Erlang source was never processed.

These repos have commit history indexed but their Swift/Elixir/Erlang code was
skipped (no parser / extension not yet allow-listed at original ingest). Swift
and Elixir now have tree-sitter parsers (symbol-level); Erlang has no PyPI
grammar so it lands at file-level only. Java is intentionally excluded — it is
already symbol-indexed.

Clears each repo from repo_commits_index (so the updater treats it as a fresh
full ingest rather than a no-op incremental) and deletes any existing docs,
then re-ingests with LLM enrichment on (matches the production daily pipeline).

Run with the worker venv (same interpreter launchd uses), NOT `uv run`:
    .venv/bin/python scripts/reingest_language_repos.py

Background:
    nohup .venv/bin/python scripts/reingest_language_repos.py \
        > logs/reingest_languages.log 2>&1 &
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv(Path(__file__).parent.parent.parent.parent / '.env')

logger.remove()
logger.add(sys.stdout, level='INFO',
           format='{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}')
logger.add('logs/reingest_languages.log', level='DEBUG',
           format='{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}',
           rotation='100 MB')

# Swift (5), Elixir (1), Erlang (1). Java excluded — already symbol-indexed.
REPOS_TO_REINGEST = [
    'kbhalerao/smartbarn',
    'kbhalerao/smartbarn2025',
    'kbhalerao/soildxios',
    'PeoplesCompany/FWDrive',
    'arunshejul88/Soil-Diagnostics',
    'kbhalerao/flyingfingers',
    'kbhalerao/lohia_plc',
]


def clear_repo_from_index(collection, repo_id: str) -> bool:
    """Remove repo from repo_commits_index to force a full re-ingestion."""
    try:
        doc = collection.get('repo_commits_index').content_as[dict]
        if repo_id in doc.get('repos', {}):
            del doc['repos'][repo_id]
            collection.upsert('repo_commits_index', doc)
            return True
        return False
    except Exception as e:
        logger.warning(f"Could not clear {repo_id} from index: {e}")
        return False


def delete_repo_docs(cluster, collection, repo_id: str) -> int:
    """Delete all documents for a repo."""
    query = f'''
    SELECT META().id as doc_id
    FROM `code_kosha`._default._default
    WHERE repo_id = '{repo_id}'
    '''
    doc_ids = [row['doc_id'] for row in cluster.query(query)]
    deleted = 0
    for doc_id in doc_ids:
        try:
            collection.remove(doc_id)
            deleted += 1
        except Exception:
            pass
    return deleted


def main():
    from couchbase.cluster import Cluster
    from couchbase.options import ClusterOptions
    from couchbase.auth import PasswordAuthenticator

    from v4.incremental.updater import IncrementalUpdater

    logger.info("=" * 70)
    logger.info("SWIFT / ELIXIR / ERLANG RE-INGESTION")
    logger.info("=" * 70)
    logger.info(f"Started at: {datetime.now().isoformat()}")
    logger.info(f"Repos to process: {len(REPOS_TO_REINGEST)}")

    cluster = Cluster(
        'couchbase://localhost',
        ClusterOptions(PasswordAuthenticator(
            os.getenv('COUCHBASE_USERNAME'),
            os.getenv('COUCHBASE_PASSWORD'),
        )),
    )
    cluster.wait_until_ready(timedelta(seconds=10))
    collection = cluster.bucket('code_kosha').default_collection()

    logger.info("\n" + "-" * 70)
    logger.info("Phase 1: Clearing existing data")
    logger.info("-" * 70)
    for repo_id in REPOS_TO_REINGEST:
        cleared = clear_repo_from_index(collection, repo_id)
        deleted = delete_repo_docs(cluster, collection, repo_id)
        logger.info(f"  {repo_id}: cleared={cleared}, deleted={deleted} docs")

    logger.info("\n" + "-" * 70)
    logger.info("Phase 2: Re-ingesting repos (LLM enrichment ON)")
    logger.info("-" * 70)
    updater = IncrementalUpdater(dry_run=False, enable_llm=True)

    results = []
    for i, repo_id in enumerate(REPOS_TO_REINGEST, 1):
        logger.info(f"\n[{i}/{len(REPOS_TO_REINGEST)}] Processing {repo_id}")
        start = datetime.now()
        try:
            repo_results = updater.run(repo_filter=repo_id)
            result = repo_results[0] if repo_results else None
            duration = (datetime.now() - start).total_seconds()
            if result:
                logger.info(f"  Completed: status={result.status}, duration={duration:.1f}s")
                results.append((repo_id, result.status, duration, None))
            else:
                logger.warning(f"  No result returned")
                results.append((repo_id, 'no_result', duration, None))
        except Exception as e:
            duration = (datetime.now() - start).total_seconds()
            logger.error(f"  Failed: {e}")
            results.append((repo_id, 'error', duration, str(e)))

    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    total_duration = sum(r[2] for r in results)
    successful = sum(1 for r in results if r[1] in ('full_reingest', 'updated'))
    failed = sum(1 for r in results if r[1] == 'error')
    for repo_id, status, duration, error in results:
        suffix = f" - {error}" if error else ""
        logger.info(f"  {repo_id}: {status} ({duration:.1f}s){suffix}")
    logger.info(f"\nTotal: {successful} successful, {failed} failed")
    logger.info(f"Total duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
    logger.info(f"Completed at: {datetime.now().isoformat()}")


if __name__ == '__main__':
    main()
