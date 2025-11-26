"""
Optimized re-embedding: Fetch all data in one query, no individual gets!
"""

import asyncio
import os
import sys
from loguru import logger
from sentence_transformers import SentenceTransformer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.database.couchbase_client import CouchbaseClient

os.environ["TOKENIZERS_PARALLELISM"] = "false"

async def reembed_optimized(batch_size=32):
    """
    Optimized re-embedding that fetches ALL data in the initial query
    No individual document gets = much faster!
    """

    logger.info("=" * 80)
    logger.info("OPTIMIZED RE-EMBEDDING WITH NOMIC-AI")
    logger.info("=" * 80)

    # Load model
    logger.info("Loading nomic-ai/nomic-embed-text-v1.5...")
    model = SentenceTransformer(
        'nomic-ai/nomic-embed-text-v1.5',
        trust_remote_code=True
    )
    logger.info(f"✓ Model loaded (device: {model.device})")

    # Connect to database
    db = CouchbaseClient()
    bucket = db.cluster.bucket('code_kosha')
    collection = bucket.default_collection()

    # Fetch ALL data we need in ONE query (no individual gets!)
    query = """
    SELECT META().id as doc_id,
           type,
           code_text,
           content,
           commit_message
    FROM `code_kosha`
    WHERE repo_id IS NOT NULL
    """

    logger.info("Fetching ALL document data in single query...")
    result = db.cluster.query(query)
    chunks = list(result)

    total = len(chunks)
    logger.info(f"✓ Loaded {total:,} documents into memory")
    logger.info(f"Batch size: {batch_size}")
    logger.info(f"Estimated batches: {(total + batch_size - 1) // batch_size}")
    logger.info("")

    # Process in batches
    updated = 0
    errors = 0
    start_time = asyncio.get_event_loop().time()

    for i in range(0, total, batch_size):
        batch = chunks[i:i + batch_size]
        batch_num = i // batch_size + 1
        total_batches = (total + batch_size - 1) // batch_size

        try:
            # Prepare texts for embedding
            texts = []
            valid_docs = []

            for chunk in batch:
                doc_id = chunk['doc_id']
                doc_type = chunk.get('type', '')

                # Extract text based on type
                # Note: Using unified 'content' field (code_text is legacy)
                if doc_type == 'code_chunk':
                    text = chunk.get('content', chunk.get('code_text', ''))
                elif doc_type == 'document':
                    text = chunk.get('content', '')
                elif doc_type == 'commit':
                    text = chunk.get('content', '')
                else:
                    # Fallback: try unified field first, then legacy fields
                    text = chunk.get('content') or chunk.get('code_text') or ''

                # Skip empty or huge texts
                if text and 0 < len(text) < 1_000_000:
                    texts.append(f"search_document: {text}")
                    valid_docs.append({'doc_id': doc_id, 'chunk': chunk})
                elif not text:
                    errors += 1

            if not texts:
                continue

            # Generate embeddings
            embeddings = model.encode(
                texts,
                convert_to_tensor=False,
                show_progress_bar=False,
                batch_size=len(texts),
                normalize_embeddings=True
            )

            # Update documents in Couchbase
            for doc_info, embedding in zip(valid_docs, embeddings):
                doc_id = doc_info['doc_id']
                chunk_data = doc_info['chunk']

                try:
                    # Fetch current document (we need full doc to update)
                    doc_result = collection.get(doc_id)
                    doc = doc_result.content_as[dict]

                    # Update embedding
                    doc['embedding'] = embedding.tolist()

                    # Write back
                    collection.upsert(doc_id, doc)
                    updated += 1

                except Exception as e:
                    logger.error(f"Failed to update {doc_id}: {e}")
                    errors += 1

            # Progress logging every 10 batches
            if batch_num % 10 == 0 or batch_num == total_batches:
                elapsed = asyncio.get_event_loop().time() - start_time
                chunks_per_sec = updated / elapsed if elapsed > 0 else 0
                eta_seconds = (total - updated) / chunks_per_sec if chunks_per_sec > 0 else 0

                logger.info(
                    f"Batch {batch_num:,}/{total_batches:,} | "
                    f"Updated: {updated:,}/{total:,} ({updated/total*100:.1f}%) | "
                    f"Errors: {errors} | "
                    f"Speed: {chunks_per_sec:.0f} chunks/sec | "
                    f"ETA: {eta_seconds/60:.1f} min"
                )

        except Exception as e:
            logger.error(f"Batch {batch_num} failed: {e}", exc_info=True)
            errors += len(batch)

    # Final summary
    total_time = asyncio.get_event_loop().time() - start_time
    logger.info("")
    logger.info("=" * 80)
    logger.info("✓ RE-EMBEDDING COMPLETE!")
    logger.info("=" * 80)
    logger.info(f"  Total chunks: {total:,}")
    logger.info(f"  Successfully updated: {updated:,}")
    logger.info(f"  Errors: {errors:,}")
    if total > 0:
        logger.info(f"  Success rate: {updated/total*100:.2f}%")
    if total_time > 0:
        logger.info(f"  Total time: {total_time/60:.1f} minutes")
        logger.info(f"  Average speed: {updated/total_time:.1f} chunks/sec")


if __name__ == "__main__":
    asyncio.run(reembed_optimized())
