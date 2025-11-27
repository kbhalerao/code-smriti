#!/usr/bin/env python3
"""
Normalize all embeddings in-place in Couchbase.

This reads all documents with embeddings, normalizes them to unit vectors,
and updates them in-place. Much faster than re-ingesting.
"""
import asyncio
import numpy as np
from loguru import logger
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions
from couchbase import subdocument


async def normalize_embeddings():
    """Normalize all embeddings in the database to unit vectors."""

    logger.info("=" * 80)
    logger.info("NORMALIZING EMBEDDINGS IN-PLACE")
    logger.info("=" * 80)

    db = CouchbaseClient()
    bucket = db.cluster.bucket("code_kosha")
    collection = bucket.default_collection()

    # Count total documents with embeddings
    count_query = """
        SELECT COUNT(*) as total
        FROM `code_kosha`
        WHERE embedding IS NOT MISSING
    """
    result = db.cluster.query(count_query)
    total_docs = list(result)[0]['total']

    logger.info(f"Found {total_docs:,} documents with embeddings")
    logger.info("")

    # Process in batches
    batch_size = 1000
    offset = 0
    updated_count = 0
    error_count = 0

    while offset < total_docs:
        logger.info(f"Processing batch: {offset:,} - {min(offset + batch_size, total_docs):,}")

        # Fetch batch of documents
        n1ql = """
            SELECT META().id, embedding
            FROM `code_kosha`
            WHERE embedding IS NOT MISSING
            LIMIT $limit OFFSET $offset
        """

        result = db.cluster.query(
            n1ql,
            QueryOptions(named_parameters={"limit": batch_size, "offset": offset})
        )

        batch_docs = list(result)

        if not batch_docs:
            break

        # Process each document in batch
        for doc in batch_docs:
            doc_id = doc['id']
            embedding = np.array(doc['embedding'])

            # Check if already normalized
            norm = np.linalg.norm(embedding)

            if abs(norm - 1.0) < 0.01:
                # Already normalized, skip
                continue

            # Normalize
            if norm > 0:
                normalized = embedding / norm
                normalized_list = normalized.tolist()

                # Update document
                try:
                    # Use subdoc operations to update just the embedding field
                    collection.mutate_in(
                        doc_id,
                        [subdocument.upsert("embedding", normalized_list)]
                    )
                    updated_count += 1

                    if updated_count % 100 == 0:
                        logger.info(f"  Updated {updated_count:,} documents...")

                except Exception as e:
                    logger.error(f"Failed to update {doc_id}: {e}")
                    error_count += 1

            else:
                logger.warning(f"Skipping {doc_id}: zero vector (norm = 0)")
                error_count += 1

        offset += batch_size

    logger.info("")
    logger.info("=" * 80)
    logger.info("NORMALIZATION COMPLETE")
    logger.info("=" * 80)
    logger.info(f"Total documents processed: {total_docs:,}")
    logger.info(f"Documents updated: {updated_count:,}")
    logger.info(f"Errors: {error_count:,}")
    logger.info(f"Already normalized (skipped): {total_docs - updated_count - error_count:,}")
    logger.info("=" * 80)

    # Verify a sample
    logger.info("")
    logger.info("Verifying normalization with sample documents...")

    verify_query = """
        SELECT META().id, file_path, embedding
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND embedding IS NOT MISSING
        LIMIT 5
    """

    result = db.cluster.query(verify_query)

    logger.info("")
    logger.info("Sample document norms:")
    all_normalized = True
    for i, row in enumerate(result, 1):
        emb = np.array(row['embedding'])
        norm = np.linalg.norm(emb)
        logger.info(f"  {i}. {row['file_path'][:50]:50s} norm: {norm:.6f}")

        if abs(norm - 1.0) > 0.01:
            all_normalized = False

    logger.info("")
    if all_normalized:
        logger.info("✅ All sample embeddings are normalized (norm ≈ 1.0)")
    else:
        logger.warning("⚠️  Some embeddings are not normalized correctly")

    logger.info("")
    logger.info("Next steps:")
    logger.info("1. Update query code to normalize query embeddings")
    logger.info("2. Rebuild FTS index (or wait for auto-rebuild)")
    logger.info("3. Run test_self_retrieval.py to verify search works")


if __name__ == "__main__":
    asyncio.run(normalize_embeddings())
