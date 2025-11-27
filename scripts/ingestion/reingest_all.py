#!/usr/bin/env python3
"""
Re-ingest All Repositories
1. Wipes the code_kosha bucket (deletes all documents)
2. Triggers pipeline_ingestion.py to re-index everything
"""

import sys
import os
import subprocess
from pathlib import Path

# Add ingestion-worker to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "ingestion-worker"))

from storage.couchbase_client import CouchbaseClient
from loguru import logger
from config import WorkerConfig

def wipe_bucket():
    """Delete all documents in the bucket"""
    logger.warning("!!! WARNING: WIPING ALL DATA FROM COUCHBASE !!!")
    
    config = WorkerConfig()
    client = CouchbaseClient()
    
    try:
        # Use N1QL to delete all documents
        # Note: This might timeout for very large buckets, but for this scale it should be fine
        query = f"DELETE FROM `{config.couchbase_bucket}`"
        logger.info(f"Executing: {query}")
        
        result = client.cluster.query(query)
        # Consume result to ensure execution
        for _ in result:
            pass
            
        logger.success("✓ Bucket wiped successfully")
        
    except Exception as e:
        logger.error(f"Failed to wipe bucket: {e}")
        sys.exit(1)
    finally:
        client.close()

def run_ingestion():
    """Run the ingestion pipeline"""
    logger.info("Starting full re-ingestion...")
    
    pipeline_script = Path(__file__).parent / "pipeline_ingestion.py"
    
    # Run pipeline_ingestion.py with --yes flag
    cmd = [sys.executable, str(pipeline_script), "--yes"]
    
    try:
        subprocess.run(cmd, check=True)
        logger.success("✓ Re-ingestion complete")
    except subprocess.CalledProcessError as e:
        logger.error(f"Ingestion failed with exit code {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    logger.info("="*70)
    logger.info("FULL RE-INGESTION UTILITY")
    logger.info("="*70)
    
    # Confirm intent
    print("\nThis will DELETE ALL DATA in the vector database and re-ingest everything.")
    response = input("Are you sure you want to proceed? (type 'yes' to confirm): ")
    
    if response != "yes":
        print("Aborted.")
        sys.exit(0)
        
    wipe_bucket()
    run_ingestion()
