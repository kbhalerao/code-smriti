"""
Re-embed ALL chunks with nomic-ai/nomic-embed-text-v1.5
Upgrades from all-mpnet-base-v2 to higher quality code embeddings
"""

import asyncio
import os
import sys
from loguru import logger
from sentence_transformers import SentenceTransformer

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.couchbase_client import CouchbaseClient

# Disable tokenizer warnings
os.environ["TOKENIZERS_PARALLELISM"] = "false"

async def reembed_all_chunks(batch_size=16):  # Reduced from 128 to avoid MPS buffer overflow
    """
    Re-generate embeddings for ALL chunks using nomic-ai model

    This upgrades from:
      - all-mpnet-base-v2 (general purpose, mediocre for code)
    To:
      - nomic-ai/nomic-embed-text-v1.5 (optimized for code/text retrieval)

    Both are 768 dimensions, so no FTS index changes needed.
    """

    logger.info("=" * 80)
    logger.info("RE-EMBEDDING ALL CHUNKS WITH NOMIC-AI")
    logger.info("=" * 80)

    # Initialize NEW embedding model
    # Use MPS (Apple GPU) with small batch size to avoid buffer overflow
    logger.info("Loading nomic-ai/nomic-embed-text-v1.5...")
    model = SentenceTransformer(
        'nomic-ai/nomic-embed-text-v1.5',
        trust_remote_code=True
        # Let it auto-detect MPS
    )
    logger.info(f"✓ Model loaded (device: {model.device})")

    # Connect to database
    db = CouchbaseClient()
    bucket = db.cluster.bucket('code_kosha')
    collection = bucket.default_collection()

    # Get ALL repo-derived documents, including those with missing/failed embeddings
    # Filter by repo_id to ensure we only process repository content
    # NOTE: Removed ORDER BY due to Couchbase query service limitation with large result sets
    query = """
    SELECT META().id as doc_id, type
    FROM `code_kosha`
    WHERE repo_id IS NOT NULL
    """

    logger.info("Fetching ALL repo-derived documents from database...")
    result = db.cluster.query(query)
    chunks = list(result)

    total = len(chunks)
    logger.info(f"Found {total:,} total chunks to re-embed")
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
            # Fetch full documents and prepare texts
            texts = []
            valid_docs = []

            for chunk in batch:
                doc_id = chunk['doc_id']

                try:
                    # Fetch full document to get all fields
                    doc_result = collection.get(doc_id)
                    doc = doc_result.content_as[dict]

                    # Get text from appropriate field based on document type
                    doc_type = chunk.get('type', doc.get('type', ''))
                    text = ''

                    # Note: Using unified 'content' field (code_text and commit_message are legacy)
                    if doc_type == 'code_chunk':
                        text = doc.get('content', doc.get('code_text', ''))
                    elif doc_type == 'document':
                        text = doc.get('content', '')
                    elif doc_type == 'commit':
                        text = doc.get('content', '')
                    else:
                        # Fallback: try unified field first, then legacy fields
                        text = doc.get('content') or doc.get('code_text') or ''

                    # Skip empty or extremely large texts (> 1MB to avoid buffer overflow)
                    if text and 0 < len(text) < 1_000_000:
                        texts.append(f"search_document: {text}")
                        valid_docs.append({'doc_id': doc_id, 'doc': doc})
                    elif len(text) >= 1_000_000:
                        logger.warning(f"Skipping {doc_id}: text too large ({len(text):,} chars)")
                        errors += 1
                    else:
                        logger.warning(f"Skipping {doc_id} (type={doc_type}): no text content found")
                        errors += 1

                except Exception as e:
                    logger.error(f"Failed to fetch document {doc_id}: {e}")
                    errors += 1

            if not texts:
                logger.warning(f"Batch {batch_num} has no valid text content")
                continue

            # Generate embeddings
            embeddings = model.encode(
                texts,
                convert_to_tensor=False,
                show_progress_bar=False,
                batch_size=len(texts),
                normalize_embeddings=True  # nomic recommendation
            )

            # Update documents in Couchbase
            for doc_info, embedding in zip(valid_docs, embeddings):
                doc_id = doc_info['doc_id']
                doc = doc_info['doc']

                try:
                    # Replace with NEW nomic embedding
                    doc['embedding'] = embedding.tolist()

                    # Update in Couchbase
                    collection.upsert(doc_id, doc)
                    updated += 1

                except Exception as e:
                    logger.error(f"Failed to update {doc_id}: {e}")
                    errors += 1

            # Progress logging
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
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Verify FTS index is using 768 dimensions")
    logger.info("  2. Test search quality with upgraded embeddings")
    logger.info("  3. Update ingestion worker to use nomic-ai model")


if __name__ == "__main__":
    asyncio.run(reembed_all_chunks())
