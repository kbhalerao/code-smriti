#!/usr/bin/env python3
"""
Test the async file-atomic pipeline on a small repository
"""

import asyncio
import os
import sys
from pathlib import Path

# Set minimal test configuration
os.environ["GITHUB_REPOS"] = "test/code-smriti"  # Small test repo
os.environ["EMBEDDING_BACKEND"] = "local"
os.environ["COUCHBASE_HOST"] = "localhost"
os.environ["COUCHBASE_USERNAME"] = "Administrator"
os.environ["COUCHBASE_PASSWORD"] = "password123"
os.environ["COUCHBASE_BUCKET"] = "code_kosha"
os.environ["LOG_LEVEL"] = "INFO"
os.environ["ENABLE_INCREMENTAL_UPDATES"] = "false"  # Start with full ingestion

# Set repos path to codesmriti-repos
os.environ["REPOS_PATH"] = str(Path.home() / "Documents/codesmriti-repos")

from loguru import logger
from worker import IngestionWorker

async def main():
    """Test the async pipeline"""
    logger.info("=" * 80)
    logger.info("ASYNC PIPELINE TEST")
    logger.info("=" * 80)

    # Initialize worker (with new async pipeline)
    worker = IngestionWorker()

    # Process test repository
    try:
        await worker.process_repository("test/code-smriti")
        logger.info("✓ Test completed successfully!")
    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
