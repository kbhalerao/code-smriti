#!/usr/bin/env python3
"""
Incremental Update Test - Verify File-Level Updates
Re-ingests code-smriti after documentation commit to test incremental updates
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime

# Set REPOS_PATH for native execution
os.environ.setdefault("REPOS_PATH", os.path.expanduser("~/codesmriti-repos"))
os.environ["ENABLE_INCREMENTAL_UPDATES"] = "true"

# Add ingestion-worker to path
sys.path.insert(0, str(Path(__file__).parent / "ingestion-worker"))

from storage.couchbase_client import CouchbaseClient
from worker import IngestionWorker
from loguru import logger

async def main():
    logger.info("="*70)
    logger.info("INCREMENTAL UPDATE TEST - code-smriti")
    logger.info("="*70)
    logger.info(f"\nTest: Re-ingest code-smriti after documentation commit")
    logger.info(f"Expected: Only modified files should trigger chunk updates")
    logger.info(f"Database will NOT be deleted (testing incremental updates)\n")

    # Initialize
    db = CouchbaseClient()
    worker = IngestionWorker()

    # Get initial stats
    initial_stats = db.get_stats()
    initial_chunks = initial_stats.get("total_chunks", 0)
    logger.info(f"Initial database state:")
    logger.info(f"  Total chunks: {initial_chunks:,}")

    # Check existing code-smriti chunks
    query = """
    SELECT COUNT(*) as count, type
    FROM `code_kosha`
    WHERE repo_id = 'kbhalerao/code-smriti'
    GROUP BY type
    """
    result = db.cluster.query(query)
    existing_by_type = {row['type']: row['count'] for row in result}
    logger.info(f"\nExisting code-smriti chunks:")
    for chunk_type, count in existing_by_type.items():
        logger.info(f"  {chunk_type}: {count}")

    # Process repository with incremental updates
    logger.info("\n" + "-"*70)
    logger.info("Starting incremental ingestion...")
    logger.info("-"*70 + "\n")

    start_time = datetime.now()

    try:
        await worker.process_repository("kbhalerao/code-smriti")
        logger.success("\n✓ Ingestion completed successfully")
    except Exception as e:
        logger.error(f"\n✗ Ingestion failed: {e}")
        raise

    elapsed = datetime.now() - start_time

    # Get final stats
    final_stats = db.get_stats()
    final_chunks = final_stats.get("total_chunks", 0)

    # Check updated code-smriti chunks
    result = db.cluster.query(query)
    updated_by_type = {row['type']: row['count'] for row in result}

    # Summary
    logger.info("\n" + "="*70)
    logger.info("INCREMENTAL UPDATE TEST COMPLETE")
    logger.info("="*70)
    logger.info(f"\nDatabase changes:")
    logger.info(f"  Initial total: {initial_chunks:,}")
    logger.info(f"  Final total:   {final_chunks:,}")
    logger.info(f"  Net change:    {final_chunks - initial_chunks:+,}")

    logger.info(f"\ncode-smriti chunk changes:")
    all_types = set(existing_by_type.keys()) | set(updated_by_type.keys())
    for chunk_type in sorted(all_types):
        before = existing_by_type.get(chunk_type, 0)
        after = updated_by_type.get(chunk_type, 0)
        change = after - before
        logger.info(f"  {chunk_type:15s}: {before:4d} → {after:4d} ({change:+d})")

    logger.info(f"\nTime elapsed: {elapsed}")

    # Verify incremental update worked
    logger.info("\n" + "-"*70)
    if final_chunks > initial_chunks:
        logger.success("✓ Database grew as expected (new commits/chunks added)")
    elif final_chunks == initial_chunks:
        logger.warning("⚠ Database size unchanged (verify files were actually modified)")
    else:
        logger.error("✗ Database shrunk unexpectedly")

    db.close()


if __name__ == "__main__":
    asyncio.run(main())
