"""
Backfill missing embeddings for chunks in Couchbase
Fixes the 2.7% of chunks that are missing vector embeddings
"""

import asyncio
import os
from loguru import logger
from sentence_transformers import SentenceTransformer
from app.database.couchbase_client import CouchbaseClient

# Disable tokenizer warnings
os.environ["TOKENIZERS_PARALLELISM"] = "false"

async def backfill_missing_embeddings(batch_size=100):
    """Generate and store embeddings for all chunks missing them"""

    logger.info("Starting embedding backfill process...")

    # Initialize model (must match ingestion)
    logger.info("Loading all-mpnet-base-v2 model...")
    model = SentenceTransformer(
        'sentence-transformers/all-mpnet-base-v2',
        trust_remote_code=True
    )
    logger.info("✓ Model loaded")

    # Connect to database
    db = CouchbaseClient()
    bucket = db.cluster.bucket('code_kosha')
    collection = bucket.default_collection()

    # Find all chunks missing embeddings
    query = """
    SELECT META().id as doc_id, code_text
    FROM `code_kosha`
    WHERE embedding IS NULL
    """

    logger.info("Querying for chunks with missing embeddings...")
    result = db.cluster.query(query)
    chunks = list(result)

    total = len(chunks)
    logger.info(f"Found {total:,} chunks missing embeddings")

    if total == 0:
        logger.info("No missing embeddings - all done!")
        return

    # Process in batches
    updated = 0
    errors = 0

    for i in range(0, total, batch_size):
        batch = chunks[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size

        logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} chunks)...")

        try:
            # Prepare texts with prefix (matching ingestion)
            texts = [f"search_document: {chunk['code_text']}" for chunk in batch]

            # Generate embeddings
            embeddings = model.encode(
                texts,
                convert_to_tensor=False,
                show_progress_bar=False,
                batch_size=batch_size
            )

            # Update documents
            for chunk, embedding in zip(batch, embeddings):
                doc_id = chunk['doc_id']

                try:
                    # Get current document
                    doc_result = collection.get(doc_id)
                    doc = doc_result.content_as[dict]

                    # Add embedding
                    doc['embedding'] = embedding.tolist()

                    # Update in Couchbase
                    collection.upsert(doc_id, doc)
                    updated += 1

                except Exception as e:
                    logger.error(f"Failed to update {doc_id}: {e}")
                    errors += 1

            if batch_num % 10 == 0:
                logger.info(f"Progress: {updated}/{total} updated, {errors} errors")

        except Exception as e:
            logger.error(f"Batch {batch_num} failed: {e}", exc_info=True)
            errors += len(batch)

    logger.info(f"✓ Backfill complete!")
    logger.info(f"  Updated: {updated:,} chunks")
    logger.info(f"  Errors: {errors:,}")
    logger.info(f"  Success rate: {updated/total*100:.1f}%")


if __name__ == "__main__":
    asyncio.run(backfill_missing_embeddings())
