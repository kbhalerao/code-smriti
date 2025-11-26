#!/usr/bin/env python3
"""
Ingest only the 5 repos needed for evaluation
"""

import asyncio
import sys
import os
from pathlib import Path
from loguru import logger

# Add ingestion-worker to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "lib/ingestion-worker"))

from storage.couchbase_client import CouchbaseClient
from worker import IngestionWorker

# 5 repos needed for evaluation
EVAL_REPOS = [
    "kbhalerao/labcore",
    "JessiePBhalerao/firstseedtests",
    "kbhalerao/ask-kev-2026",
    "kbhalerao/smartbarn2025",
    "kbhalerao/508hCoverCrop"
]

async def main():
    logger.info("=" * 80)
    logger.info("EVAL REPOS INGESTION - Fresh Start with nomic-ai")
    logger.info("=" * 80)
    logger.info(f"\nIngesting {len(EVAL_REPOS)} repos for evaluation:")
    for i, repo in enumerate(EVAL_REPOS, 1):
        logger.info(f"  {i}. {repo}")

    # Initialize worker
    db = CouchbaseClient()
    worker = IngestionWorker()

    # Process each repository
    logger.info("\n" + "=" * 80)
    logger.info("Starting Ingestion")
    logger.info("=" * 80)

    successful = 0
    failed = 0
    failed_repos = []

    for i, repo_id in enumerate(EVAL_REPOS, 1):
        logger.info(f"\n[{i}/{len(EVAL_REPOS)}] Processing: {repo_id}")
        logger.info("-" * 80)

        try:
            result = await worker.process_repository(repo_id)

            if result.get('status') == 'success':
                successful += 1
                stats = result.get('stats', {})
                logger.info(f"✓ {repo_id} completed successfully")
                logger.info(f"  Code chunks: {stats.get('code_chunks', 0)}")
                logger.info(f"  Documents: {stats.get('documents', 0)}")
                logger.info(f"  Commits: {stats.get('commits', 0)}")
            else:
                failed += 1
                failed_repos.append(repo_id)
                error = result.get('error', 'Unknown error')
                logger.error(f"✗ {repo_id} failed: {error}")

        except Exception as e:
            failed += 1
            failed_repos.append(repo_id)
            logger.error(f"✗ {repo_id} failed with exception: {e}", exc_info=True)

    # Final summary
    logger.info("\n" + "=" * 80)
    logger.info("INGESTION COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total repos: {len(EVAL_REPOS)}")
    logger.info(f"Successful: {successful}")
    logger.info(f"Failed: {failed}")

    if failed_repos:
        logger.info("\nFailed repos:")
        for repo in failed_repos:
            logger.info(f"  - {repo}")

    logger.info("\n✓ Ready for evaluation!")


if __name__ == "__main__":
    asyncio.run(main())
