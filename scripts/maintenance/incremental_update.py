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


def load_repos_from_filesystem(repos_path: str) -> List[str]:
    """
    Auto-discover repositories from filesystem
    Converts directory names (owner_repo) back to repo_id format (owner/repo)

    Args:
        repos_path: Path to directory containing cloned repos

    Returns:
        List of repo_id strings
    """
    repos = []
    repos_dir = Path(repos_path)

    if not repos_dir.exists():
        logger.warning(f"Repos directory does not exist: {repos_path}")
        return repos

    # Skip test repos and special directories
    skip_patterns = {'test/', 'octocat/', '.', '__'}

    for dirname in sorted(os.listdir(repos_dir)):
        full_path = repos_dir / dirname

        # Skip non-directories and hidden files
        if not full_path.is_dir() or dirname.startswith('.'):
            continue

        # Convert directory name back to repo_id format
        # Format: owner_repo -> owner/repo
        parts = dirname.split('_', 1)
        if len(parts) == 2:
            repo_id = f"{parts[0]}/{parts[1]}"

            # Skip test repos
            if any(repo_id.startswith(pattern) for pattern in skip_patterns):
                logger.debug(f"Skipping test repo: {repo_id}")
                continue

            repos.append(repo_id)
        else:
            logger.debug(f"Skipping invalid directory name: {dirname}")

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


def get_current_repo_files(repo_path: Path, repo_id: str) -> Set[str]:
    """
    Get all supported files currently in the repository filesystem

    Args:
        repo_path: Path to the repository directory
        repo_id: Repository identifier

    Returns:
        Set of relative file paths
    """
    from config import WorkerConfig
    from parsers.code_parser import should_skip_file

    config = WorkerConfig()
    current_files = set()

    # Collect code files
    for ext in config.supported_code_extensions:
        for file_path in repo_path.rglob(f"*{ext}"):
            if not should_skip_file(file_path):
                relative_path = str(file_path.relative_to(repo_path))
                current_files.add(relative_path)

    # Collect doc files
    for ext in config.supported_doc_extensions:
        for file_path in repo_path.rglob(f"*{ext}"):
            if not should_skip_file(file_path):
                relative_path = str(file_path.relative_to(repo_path))
                current_files.add(relative_path)

    return current_files


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

    # Auto-discover repositories from filesystem
    repos_path = os.getenv("REPOS_PATH", "/Users/kaustubh/Documents/codesmriti-repos")
    logger.info(f"\nAuto-discovering repositories from: {repos_path}")

    all_repos = load_repos_from_filesystem(repos_path)
    logger.info(f"  Found {len(all_repos)} repositories in filesystem")

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
            logger.info(f"\nIngesting new repo [{processed}/{len(new_repos)}]: {repo_id}")

            try:
                await worker.process_repository(repo_id)
                successful += 1
                logger.success(f"✓ {repo_id} completed")
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

            try:
                await worker.process_repository(repo_id)
                successful += 1
                # Only log if verbose or if it's a milestone
                if processed % 10 == 0 or processed == len(repos_to_update):
                    logger.info(f"Progress: {processed}/{len(repos_to_update)} repos updated")
            except Exception as e:
                # Check if error is because no changes detected
                if "no changes" in str(e).lower():
                    skipped += 1
                else:
                    failed += 1
                    failed_repos.append((repo_id, str(e)))
                    logger.error(f"✗ {repo_id} failed: {e}")

    # PHASE 3: Cleanup deleted files
    if repos_to_update:
        logger.info(f"\n{'='*70}")
        logger.info(f"PHASE 3: Cleanup Deleted Files for {len(repos_to_update)} Repos")
        logger.info(f"{'='*70}")

        total_deleted_files = 0
        total_deleted_chunks = 0

        for repo_id in repos_to_update:
            try:
                # Get files in database for this repo
                db_file_commits = db.get_repo_file_commits(repo_id)
                db_files = set(db_file_commits.keys())

                # Get current files in filesystem
                repo_path = Path(repos_path) / repo_id.replace("/", "_")
                if not repo_path.exists():
                    logger.warning(f"Repo path does not exist: {repo_path}, skipping cleanup")
                    continue

                current_files = get_current_repo_files(repo_path, repo_id)

                # Find deleted files (in DB but not in filesystem)
                deleted_files = db_files - current_files

                if deleted_files:
                    # Delete all chunks for deleted files
                    repo_deleted_chunks = 0
                    for file_path in deleted_files:
                        deleted_count = db.delete_file_chunks(repo_id, file_path)
                        repo_deleted_chunks += deleted_count

                    total_deleted_files += len(deleted_files)
                    total_deleted_chunks += repo_deleted_chunks

                    # Only log if significant cleanup was done
                    if len(deleted_files) > 0:
                        logger.info(f"🗑  {repo_id}: Removed {len(deleted_files)} files ({repo_deleted_chunks} chunks)")

            except Exception as e:
                logger.error(f"Error during cleanup for {repo_id}: {e}")

        if total_deleted_files > 0:
            logger.info(f"\n✓ Cleanup complete: {total_deleted_files} files removed, {total_deleted_chunks} chunks deleted")
        else:
            logger.info(f"\n✓ Cleanup complete: No deleted files found")

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
    if 'total_deleted_files' in locals() and total_deleted_files > 0:
        logger.info(f"  Deleted files:    {total_deleted_files} ({total_deleted_chunks} chunks)")

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
