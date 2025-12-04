#!/usr/bin/env python3
"""
Normalize all embeddings in the database in-place.

This script:
1. Queries all documents with embeddings
2. Normalizes each embedding to unit length (L2 norm = 1)
3. Updates the document in place

Required for dot_product similarity to work correctly in Couchbase FTS.
"""

import sys
import argparse
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from loguru import logger
from tqdm import tqdm

from storage.couchbase_client import CouchbaseClient
import couchbase.subdocument as SD


def normalize_embedding(embedding: list) -> list:
    """Normalize embedding to unit length."""
    arr = np.array(embedding)
    norm = np.linalg.norm(arr)
    if norm > 0:
        return (arr / norm).tolist()
    return embedding


def get_documents_with_embeddings(cb: CouchbaseClient, batch_size: int = 1000, offset: int = 0):
    """Get documents that have embeddings."""
    query = f"""
        SELECT META().id as doc_id, embedding
        FROM `code_kosha`
        WHERE embedding IS NOT MISSING
          AND ARRAY_LENGTH(embedding) > 0
        LIMIT {batch_size}
        OFFSET {offset}
    """
    return list(cb.cluster.query(query))


def check_normalization_status(cb: CouchbaseClient, sample_size: int = 100):
    """Check current normalization status of embeddings."""
    query = f"""
        SELECT embedding
        FROM `code_kosha`
        WHERE embedding IS NOT MISSING
          AND ARRAY_LENGTH(embedding) > 0
        LIMIT {sample_size}
    """

    norms = []
    for row in cb.cluster.query(query):
        emb = row.get("embedding")
        if emb:
            norm = np.linalg.norm(emb)
            norms.append(norm)

    if not norms:
        return None, None, None

    return min(norms), max(norms), np.mean(norms)


def count_documents_with_embeddings(cb: CouchbaseClient) -> int:
    """Count total documents with embeddings."""
    query = """
        SELECT COUNT(*) as count
        FROM `code_kosha`
        WHERE embedding IS NOT MISSING
          AND ARRAY_LENGTH(embedding) > 0
    """
    for row in cb.cluster.query(query):
        return row.get("count", 0)
    return 0


def normalize_all_embeddings(cb: CouchbaseClient, batch_size: int = 500, dry_run: bool = False):
    """Normalize all embeddings in the database."""

    # Check current status
    logger.info("Checking current normalization status...")
    min_norm, max_norm, mean_norm = check_normalization_status(cb)

    if min_norm is not None:
        logger.info(f"Current norms: min={min_norm:.4f}, max={max_norm:.4f}, mean={mean_norm:.4f}")

        if 0.99 < mean_norm < 1.01:
            logger.info("Embeddings appear to already be normalized!")
            return 0

    # Count total
    total = count_documents_with_embeddings(cb)
    logger.info(f"Total documents with embeddings: {total:,}")

    if dry_run:
        logger.info("Dry run - not making changes")
        return 0

    # Process in batches
    bucket = cb.cluster.bucket("code_kosha")
    collection = bucket.default_collection()

    updated = 0
    offset = 0

    with tqdm(total=total, desc="Normalizing embeddings") as pbar:
        while True:
            rows = get_documents_with_embeddings(cb, batch_size, offset)

            if not rows:
                break

            for row in rows:
                doc_id = row.get("doc_id")
                embedding = row.get("embedding")

                if not doc_id or not embedding:
                    continue

                # Check if already normalized
                norm = np.linalg.norm(embedding)
                if 0.99 < norm < 1.01:
                    pbar.update(1)
                    continue

                # Normalize
                normalized = normalize_embedding(embedding)

                # Update in place using subdoc
                try:
                    collection.mutate_in(
                        doc_id,
                        [SD.upsert("embedding", normalized)]
                    )
                    updated += 1
                except Exception as e:
                    # Fallback to full document update
                    try:
                        doc = collection.get(doc_id).content_as[dict]
                        doc["embedding"] = normalized
                        collection.upsert(doc_id, doc)
                        updated += 1
                    except Exception as e2:
                        logger.warning(f"Failed to update {doc_id}: {e2}")

                pbar.update(1)

            offset += batch_size

            # Log progress
            if updated > 0 and updated % 5000 == 0:
                logger.info(f"Updated {updated:,} documents...")

    logger.info(f"Normalization complete. Updated {updated:,} documents.")

    # Verify
    logger.info("Verifying normalization...")
    min_norm, max_norm, mean_norm = check_normalization_status(cb)
    if min_norm is not None:
        logger.info(f"New norms: min={min_norm:.4f}, max={max_norm:.4f}, mean={mean_norm:.4f}")

    return updated


def main():
    parser = argparse.ArgumentParser(description="Normalize all embeddings in the database")
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for processing")
    parser.add_argument("--dry-run", action="store_true", help="Check status without making changes")
    args = parser.parse_args()

    logger.info("Connecting to Couchbase...")
    cb = CouchbaseClient()

    updated = normalize_all_embeddings(cb, batch_size=args.batch_size, dry_run=args.dry_run)

    if not args.dry_run:
        logger.info(f"Done! Updated {updated:,} embeddings.")
        logger.info("Note: FTS index will automatically rebuild with new embeddings.")


if __name__ == "__main__":
    main()
