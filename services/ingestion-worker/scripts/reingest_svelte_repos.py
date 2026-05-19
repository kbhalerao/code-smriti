#!/usr/bin/env python3
"""
Re-ingest Svelte repos with improved tree-sitter parsing.

Run from ingestion-worker directory:
    source .venv/bin/activate
    python scripts/reingest_svelte_repos.py

Or with nohup for background execution:
    nohup python scripts/reingest_svelte_repos.py > reingest_svelte.log 2>&1 &
"""

import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime
from dotenv import load_dotenv
from loguru import logger

# Load env from project root
load_dotenv(Path(__file__).parent.parent.parent.parent / '.env')

# Configure logging
logger.remove()
logger.add(
    sys.stdout,
    level='INFO',
    format='{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}'
)
logger.add(
    'reingest_svelte.log',
    level='DEBUG',
    format='{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}',
    rotation='100 MB'
)

REPOS_TO_REINGEST = [
    'kbhalerao/pinionfe',
    'kbhalerao/agkit.io-ui',
    'kbhalerao/chief-of-staff',
    'kbhalerao/code-smriti',
    'PeoplesCompany/farmworth_frontend',
    'PeoplesCompany/public-farmworth',
    'PeoplesCompany/farmland_insights',
    'PeoplesCompany/listings-map',
    'PeoplesCompany/farmworthdraw',
    'PeoplesCompany/farmworthdash',
]


def clear_repo_from_index(collection, repo_id: str) -> bool:
    """Remove repo from commits index to trigger full re-ingestion."""
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
    result = cluster.query(query)
    doc_ids = [row['doc_id'] for row in result]

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
    from datetime import timedelta
    import os

    from v4.incremental.updater import IncrementalUpdater

    logger.info("=" * 70)
    logger.info("SVELTE REPOS RE-INGESTION")
    logger.info("=" * 70)
    logger.info(f"Started at: {datetime.now().isoformat()}")
    logger.info(f"Repos to process: {len(REPOS_TO_REINGEST)}")

    # Connect to Couchbase
    cluster = Cluster(
        'couchbase://localhost',
        ClusterOptions(PasswordAuthenticator(
            os.getenv('COUCHBASE_USERNAME'),
            os.getenv('COUCHBASE_PASSWORD')
        ))
    )
    cluster.wait_until_ready(timedelta(seconds=10))
    bucket = cluster.bucket('code_kosha')
    collection = bucket.default_collection()

    # Phase 1: Clear repos from index and delete existing docs
    logger.info("\n" + "-" * 70)
    logger.info("Phase 1: Clearing existing data")
    logger.info("-" * 70)

    for repo_id in REPOS_TO_REINGEST:
        cleared = clear_repo_from_index(collection, repo_id)
        deleted = delete_repo_docs(cluster, collection, repo_id)
        logger.info(f"  {repo_id}: cleared={cleared}, deleted={deleted} docs")

    # Phase 2: Re-ingest each repo
    logger.info("\n" + "-" * 70)
    logger.info("Phase 2: Re-ingesting repos")
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

    # Summary
    logger.info("\n" + "=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)

    total_duration = sum(r[2] for r in results)
    successful = sum(1 for r in results if r[1] in ('full_reingest', 'updated'))
    failed = sum(1 for r in results if r[1] == 'error')

    for repo_id, status, duration, error in results:
        if error:
            logger.info(f"  {repo_id}: {status} ({duration:.1f}s) - {error}")
        else:
            logger.info(f"  {repo_id}: {status} ({duration:.1f}s)")

    logger.info(f"\nTotal: {successful} successful, {failed} failed")
    logger.info(f"Total duration: {total_duration:.1f}s ({total_duration/60:.1f} min)")
    logger.info(f"Completed at: {datetime.now().isoformat()}")


if __name__ == '__main__':
    main()
