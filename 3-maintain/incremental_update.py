#!/usr/bin/env python3
"""
Incremental Update - Process New and Updated Repositories
- Ingests newly added repos from repos_to_ingest.txt
- Updates existing repos with latest changes (incremental updates)
- Can be run as a cron job for automated updates
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Set, List

# Set REPOS_PATH for native execution
os.environ["REPOS_PATH"] = "/Users/kaustubh/Documents/codesmriti-repos"
os.environ["ENABLE_INCREMENTAL_UPDATES"] = "true"

# Add ingestion-worker to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "ingestion-worker"))

from storage.couchbase_client import CouchbaseClient
from worker import IngestionWorker
from loguru import logger


def load_repos_from_file(file_path: str) -> List[str]:
    """
    Load repository list from repos_to_ingest.txt
    Skips comments (#) and empty lines

    Args:
        file_path: Path to repos_to_ingest.txt

    Returns:
        List of repo_id strings
    """
    repos = []

    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith('#'):
                continue

            # Extract repo name (before any comment)
            repo_id = line.split('#')[0].strip()

            if repo_id:
                repos.append(repo_id)

    return repos


def get_repos_in_database(db: CouchbaseClient) -> Set[str]:
    """
    Query database for all repos that have been ingested

    Args:
        db: CouchbaseClient instance

    Returns:
        Set of repo_id strings already in database
    """
    query = "SELECT DISTINCT repo_id FROM code_kosha WHERE repo_id IS NOT MISSING"
    result = db.cluster.query(query)
    return {row['repo_id'] for row in result}


async def main():
    logger.info("="*70)
    logger.info("INCREMENTAL UPDATE - New Repos + Updates to Existing")
    logger.info("="*70)

    # Initialize
    db = CouchbaseClient()
    worker = IngestionWorker()

    # Get initial database stats
    initial_stats = db.get_stats()
    initial_chunks = initial_stats.get("total_chunks", 0)
    logger.info(f"\nInitial database state:")
    logger.info(f"  Total chunks: {initial_chunks:,}")

    # Load repository list from file
    repos_file = Path(__file__).parent.parent / "1-config" / "repos_to_ingest.txt"
    logger.info(f"\nLoading repository list from: {repos_file}")

    all_repos = load_repos_from_file(str(repos_file))
    logger.info(f"  Found {len(all_repos)} repositories in repos_to_ingest.txt")

    # Get repos already in database
    existing_repos = get_repos_in_database(db)
    logger.info(f"  Found {len(existing_repos)} repositories already in database")

    # Categorize repos
    new_repos = [repo for repo in all_repos if repo not in existing_repos]
    repos_to_update = [repo for repo in all_repos if repo in existing_repos]

    logger.info(f"\n" + "-"*70)
    logger.info(f"Categorization:")
    logger.info(f"  New repos to ingest:      {len(new_repos)}")
    logger.info(f"  Existing repos to update: {len(repos_to_update)}")

    # Show first few of each category
    if new_repos:
        logger.info(f"\nFirst {min(5, len(new_repos))} new repos:")
        for repo in new_repos[:5]:
            logger.info(f"  - {repo}")
        if len(new_repos) > 5:
            logger.info(f"  ... and {len(new_repos) - 5} more")

    if repos_to_update:
        logger.info(f"\nFirst {min(5, len(repos_to_update))} repos to update:")
        for repo in repos_to_update[:5]:
            logger.info(f"  - {repo}")
        if len(repos_to_update) > 5:
            logger.info(f"  ... and {len(repos_to_update) - 5} more")

    if not new_repos and not repos_to_update:
        logger.warning("\nNo repositories to process!")
        return

    # Process repositories
    logger.info("\n" + "="*70)
    logger.info("Starting Incremental Update")
    logger.info("="*70)

    successful = 0
    failed = 0
    failed_repos = []
    skipped = 0

    start_time = datetime.now()
    total_repos = len(new_repos) + len(repos_to_update)
    processed = 0

    # Process new repos first (full ingestion)
    if new_repos:
        logger.info(f"\n{'='*70}")
        logger.info(f"PHASE 1: Full Ingestion of {len(new_repos)} New Repos")
        logger.info(f"{'='*70}")

        for repo_id in new_repos:
            processed += 1
            logger.info(f"\n[{processed}/{total_repos}] NEW REPO: {repo_id}")
            logger.info("-"*70)

            try:
                await worker.process_repository(repo_id)
                successful += 1
                logger.success(f"✓ {repo_id} ingested successfully")
            except Exception as e:
                failed += 1
                failed_repos.append((repo_id, str(e)))
                logger.error(f"✗ {repo_id} failed: {e}")

    # Process existing repos (incremental updates)
    if repos_to_update:
        logger.info(f"\n{'='*70}")
        logger.info(f"PHASE 2: Incremental Update of {len(repos_to_update)} Existing Repos")
        logger.info(f"{'='*70}")

        for repo_id in repos_to_update:
            processed += 1
            logger.info(f"\n[{processed}/{total_repos}] UPDATE: {repo_id}")
            logger.info("-"*70)

            try:
                await worker.process_repository(repo_id)
                successful += 1
                logger.success(f"✓ {repo_id} updated successfully")
            except Exception as e:
                # Check if error is because no changes detected
                if "no changes" in str(e).lower():
                    skipped += 1
                    logger.info(f"⊘ {repo_id} - no changes detected")
                else:
                    failed += 1
                    failed_repos.append((repo_id, str(e)))
                    logger.error(f"✗ {repo_id} failed: {e}")

    elapsed = datetime.now() - start_time

    # Get final database stats
    final_stats = db.get_stats()
    final_chunks = final_stats.get("total_chunks", 0)

    # Summary
    logger.info("\n" + "="*70)
    logger.info("INCREMENTAL UPDATE COMPLETE")
    logger.info("="*70)

    logger.info(f"\nResults:")
    logger.info(f"  Total processed:  {processed}")
    logger.info(f"  Successful:       {successful}")
    logger.info(f"  Failed:           {failed}")
    logger.info(f"  Skipped (no changes): {skipped}")

    logger.info(f"\nDatabase changes:")
    logger.info(f"  Initial chunks: {initial_chunks:,}")
    logger.info(f"  Final chunks:   {final_chunks:,}")
    logger.info(f"  Net change:     {final_chunks - initial_chunks:+,}")

    logger.info(f"\nTime elapsed: {elapsed}")

    if failed_repos:
        logger.info(f"\n{'-'*70}")
        logger.info(f"Failed repositories ({len(failed_repos)}):")
        for repo_id, error in failed_repos:
            logger.error(f"  - {repo_id}: {error}")

    db.close()

    # Exit with error code if any failures
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
