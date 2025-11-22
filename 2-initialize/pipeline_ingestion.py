#!/usr/bin/env python3
"""
Pipeline Ingestion - Process Multiple Repositories
Reads repos_to_ingest.txt and processes them in order
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime

# Set REPOS_PATH for native execution (outside project to prevent recursion)
os.environ["REPOS_PATH"] = "/Users/kaustubh/Documents/codesmriti-repos"

# Add ingestion-worker to path
sys.path.insert(0, str(Path(__file__).parent / "ingestion-worker"))

from storage.couchbase_client import CouchbaseClient
from worker import IngestionWorker
from loguru import logger

def load_repos_list(file_path: str) -> list:
    """
    Load repository list from file
    Skips comments (#) and already completed repos ([DONE])

    Args:
        file_path: Path to repos_to_ingest.txt

    Returns:
        List of repo_id strings to process
    """
    repos = []

    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Skip already completed repos
            if '[DONE]' in line or '[IN PROGRESS]' in line:
                continue

            # Extract repo name (before any comment)
            repo_id = line.split('#')[0].strip()

            if repo_id:
                repos.append(repo_id)

    return repos


async def main():
    logger.info("="*70)
    logger.info("PIPELINE INGESTION - Processing Repository Queue")
    logger.info("="*70)

    # Load repository list
    repos_file = Path(__file__).parent / "repos_to_ingest.txt"
    logger.info(f"\nLoading repository list from: {repos_file}")

    repos = load_repos_list(str(repos_file))
    logger.info(f"Found {len(repos)} repositories to process")

    if not repos:
        logger.warning("No repositories to process. Check repos_to_ingest.txt")
        return

    # Show first 10 repos
    logger.info("\nFirst 10 repositories in queue:")
    for i, repo in enumerate(repos[:10], 1):
        logger.info(f"  {i}. {repo}")

    if len(repos) > 10:
        logger.info(f"  ... and {len(repos) - 10} more")

    # Parse arguments
    import argparse
    parser = argparse.ArgumentParser(description="Pipeline Ingestion")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts")
    args = parser.parse_args()

    # Confirm before proceeding
    logger.info("\n" + "-"*70)
    if not args.yes:
        response = input(f"Process all {len(repos)} repositories? (y/n): ")
        if response.lower() != 'y':
            logger.info("Aborted by user")
            return
    else:
        logger.info(f"Auto-confirming process for {len(repos)} repositories")

    # Initialize worker
    db = CouchbaseClient()
    worker = IngestionWorker()

    # Process each repository
    logger.info("\n" + "="*70)
    logger.info("Starting Pipeline Ingestion")
    logger.info("="*70)

    successful = 0
    failed = 0
    failed_repos = []

    start_time = datetime.now()

    for i, repo_id in enumerate(repos, 1):
        logger.info(f"\n[{i}/{len(repos)}] Processing: {repo_id}")
        logger.info("-"*70)

        try:
            await worker.process_repository(repo_id)
            successful += 1
            logger.success(f"✓ {repo_id} completed successfully")
        except Exception as e:
            failed += 1
            failed_repos.append(repo_id)
            logger.error(f"✗ {repo_id} failed: {e}")

            # Ask whether to continue on failure
            if not args.yes:
                response = input(f"\nContinue with remaining {len(repos) - i} repos? (y/n): ")
                if response.lower() != 'y':
                    logger.info("Pipeline stopped by user")
                    break
            else:
                logger.warning("Continuing despite failure (non-interactive mode)")

    # Final summary
    elapsed = datetime.now() - start_time

    logger.info("\n" + "="*70)
    logger.info("PIPELINE INGESTION COMPLETE")
    logger.info("="*70)
    logger.info(f"\nProcessed: {successful + failed} / {len(repos)} repositories")
    logger.info(f"Successful: {successful}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Total time: {elapsed}")

    if failed_repos:
        logger.warning(f"\nFailed repositories:")
        for repo in failed_repos:
            logger.warning(f"  - {repo}")

    # Show final database stats
    stats = db.get_stats()
    total_chunks = stats.get("total_chunks", 0)
    logger.info(f"\n✓ Total chunks in database: {total_chunks:,}")

    db.close()


if __name__ == "__main__":
    asyncio.run(main())
