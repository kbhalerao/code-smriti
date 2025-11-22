#!/usr/bin/env python3
"""
Test embedding normalization: Compare stored embeddings vs freshly generated ones.

This tests whether:
1. The ingestion code produces normalized embeddings
2. The query code produces normalized embeddings
3. Both produce the same embeddings for the same text
"""
import asyncio
import numpy as np
from sentence_transformers import SentenceTransformer
from app.database.couchbase_client import CouchbaseClient


async def test_normalization():
    print("=" * 80)
    print("EMBEDDING NORMALIZATION TEST")
    print("=" * 80)
    print()

    db = CouchbaseClient()

    # Get a known code chunk with its stored embedding
    n1ql = """
        SELECT META().id, file_path, repo_id, content, embedding
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND file_path LIKE '%orders/consumers.py%'
          AND repo_id = 'kbhalerao/labcore'
        LIMIT 1
    """

    result = db.cluster.query(n1ql)
    source_chunk = None
    for row in result:
        source_chunk = row
        break

    if not source_chunk:
        print("❌ Could not find test chunk")
        return

    print(f"Test chunk:")
    print(f"  File: {source_chunk['repo_id']}/{source_chunk['file_path']}")
    print(f"  ID: {source_chunk['id']}")
    print(f"  Content length: {len(source_chunk['content'])} chars")
    print()

    # Stored embedding from database
    stored_embedding = np.array(source_chunk['embedding'])
    stored_norm = np.linalg.norm(stored_embedding)
    stored_mean = np.mean(stored_embedding)
    stored_std = np.std(stored_embedding)

    print("=" * 80)
    print("STORED EMBEDDING (from database)")
    print("=" * 80)
    print(f"  Norm: {stored_norm:.6f}")
    print(f"  Mean: {stored_mean:.6f}")
    print(f"  Std:  {stored_std:.6f}")
    print(f"  First 5 values: {stored_embedding[:5]}")
    print()

    # Load the same model used in ingestion
    print("Loading embedding model...")
    model = SentenceTransformer(
        "nomic-ai/nomic-embed-text-v1.5",
        trust_remote_code=True
    )
    print(f"✓ Model loaded on device: {model.device}")
    print()

    # Test 1: Generate embedding using INGESTION method (no explicit normalization)
    print("=" * 80)
    print("TEST 1: Ingestion Method (model.encode with defaults)")
    print("=" * 80)

    text_with_prefix = f"search_document: {source_chunk['content']}"

    ingestion_embedding = model.encode(
        text_with_prefix,
        convert_to_tensor=False,
        show_progress_bar=False
    )
    ingestion_embedding = np.array(ingestion_embedding)

    ingestion_norm = np.linalg.norm(ingestion_embedding)
    ingestion_mean = np.mean(ingestion_embedding)
    ingestion_std = np.std(ingestion_embedding)

    print(f"  Norm: {ingestion_norm:.6f}")
    print(f"  Mean: {ingestion_mean:.6f}")
    print(f"  Std:  {ingestion_std:.6f}")
    print(f"  First 5 values: {ingestion_embedding[:5]}")
    print()

    # Compare with stored
    cosine_similarity = np.dot(stored_embedding, ingestion_embedding) / (stored_norm * ingestion_norm)
    euclidean_distance = np.linalg.norm(stored_embedding - ingestion_embedding)

    print(f"  Cosine similarity with stored: {cosine_similarity:.6f}")
    print(f"  Euclidean distance from stored: {euclidean_distance:.6f}")
    print()

    if cosine_similarity > 0.999:
        print("  ✅ MATCH: Fresh embedding matches stored (same direction)")
    else:
        print("  ⚠️  MISMATCH: Fresh embedding differs from stored")

    if abs(ingestion_norm - stored_norm) < 0.01:
        print("  ✅ MATCH: Norms are identical")
    else:
        print(f"  ⚠️  MISMATCH: Norm difference = {abs(ingestion_norm - stored_norm):.6f}")
    print()

    # Test 2: Generate embedding with EXPLICIT normalization
    print("=" * 80)
    print("TEST 2: With Explicit Normalization (normalize_embeddings=True)")
    print("=" * 80)

    normalized_embedding = model.encode(
        text_with_prefix,
        convert_to_tensor=False,
        show_progress_bar=False,
        normalize_embeddings=True  # EXPLICIT normalization
    )
    normalized_embedding = np.array(normalized_embedding)

    normalized_norm = np.linalg.norm(normalized_embedding)
    normalized_mean = np.mean(normalized_embedding)
    normalized_std = np.std(normalized_embedding)

    print(f"  Norm: {normalized_norm:.6f}")
    print(f"  Mean: {normalized_mean:.6f}")
    print(f"  Std:  {normalized_std:.6f}")
    print(f"  First 5 values: {normalized_embedding[:5]}")
    print()

    # Compare with stored
    cosine_similarity_normalized = np.dot(stored_embedding, normalized_embedding) / (stored_norm * normalized_norm)
    euclidean_distance_normalized = np.linalg.norm(stored_embedding - normalized_embedding)

    print(f"  Cosine similarity with stored: {cosine_similarity_normalized:.6f}")
    print(f"  Euclidean distance from stored: {euclidean_distance_normalized:.6f}")
    print()

    if abs(normalized_norm - 1.0) < 0.01:
        print("  ✅ NORMALIZED: Norm ≈ 1.0 (unit vector)")
    else:
        print(f"  ❌ NOT NORMALIZED: Norm = {normalized_norm:.6f} (expected ≈ 1.0)")
    print()

    # Test 3: Query embedding (simulating search)
    print("=" * 80)
    print("TEST 3: Query Embedding (for search)")
    print("=" * 80)

    query_text = "Django Channels background worker with job counter decorator"
    query_with_prefix = f"search_query: {query_text}"

    query_embedding = model.encode(
        query_with_prefix,
        convert_to_tensor=False,
        show_progress_bar=False
    )
    query_embedding = np.array(query_embedding)

    query_norm = np.linalg.norm(query_embedding)
    query_mean = np.mean(query_embedding)
    query_std = np.std(query_embedding)

    print(f"  Query: {query_text}")
    print(f"  Norm: {query_norm:.6f}")
    print(f"  Mean: {query_mean:.6f}")
    print(f"  Std:  {query_std:.6f}")
    print()

    # Compute similarity with stored embedding
    similarity_with_stored = np.dot(query_embedding, stored_embedding) / (query_norm * stored_norm)
    print(f"  Similarity with test chunk: {similarity_with_stored:.6f}")
    print()

    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()
    print(f"Stored embedding norm:     {stored_norm:.6f}")
    print(f"Fresh embedding norm:      {ingestion_norm:.6f}")
    print(f"Normalized embedding norm: {normalized_norm:.6f}")
    print(f"Query embedding norm:      {query_norm:.6f}")
    print()

    if abs(stored_norm - 1.0) < 0.1:
        print("✅ Stored embeddings ARE normalized (norm ≈ 1.0)")
    else:
        print(f"❌ Stored embeddings NOT normalized (norm = {stored_norm:.6f}, expected ≈ 1.0)")

    print()
    print("HYPOTHESIS:")
    if abs(stored_norm - ingestion_norm) < 0.01:
        print("  Ingestion and storage are CONSISTENT")
        if abs(stored_norm - 1.0) > 0.1:
            print("  BUT embeddings are stored UNNORMALIZED")
            print("  → FIX: Add normalize_embeddings=True in ingestion AND query")
        else:
            print("  AND embeddings are properly normalized")
            print("  → Something else is wrong with search")
    else:
        print("  Ingestion and storage are INCONSISTENT")
        print("  → FIX: Ensure same encoding parameters used consistently")

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_normalization())
