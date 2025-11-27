#!/usr/bin/env python3
"""
Test vector search with identity match - find a code chunk and try to retrieve it
using its own embedding (with slight jitter).
"""
import asyncio
import json
import numpy as np
from app.database.couchbase_client import CouchbaseClient
from sentence_transformers import SentenceTransformer
from couchbase.options import QueryOptions

async def test_identity_match():
    """Test if we can find a code chunk using its own embedding"""

    db = CouchbaseClient()

    # Step 1: Find a consumer-related code chunk with good content
    print("=" * 70)
    print("STEP 1: Finding a consumer-related code chunk...")
    print("=" * 70)

    query = """
        SELECT META().id, repo_id, file_path, content, `language`, embedding
        FROM `code_kosha`
        WHERE type='code_chunk'
          AND repo_id='kbhalerao/labcore'
          AND file_path LIKE '%consumer%'
          AND LENGTH(content) > 300
        LIMIT 5
    """

    result = db.cluster.query(query)
    chunks = list(result)

    if not chunks:
        print("❌ No consumer chunks found!")
        return

    # Pick the first one
    test_chunk = chunks[0]
    chunk_id = test_chunk['id']

    print(f"\n✓ Found chunk: {chunk_id}")
    print(f"  Repo: {test_chunk['repo_id']}")
    print(f"  File: {test_chunk['file_path']}")
    print(f"  Language: {test_chunk['language']}")
    print(f"  Content length: {len(test_chunk['content'])} chars")
    print(f"\n  Content preview:")
    print(f"  {test_chunk['content'][:200]}...")

    # Get the embedding
    original_embedding = test_chunk.get('embedding')
    if not original_embedding:
        print("❌ No embedding found in chunk!")
        return

    print(f"\n  Embedding dimensions: {len(original_embedding)}")
    print(f"  Embedding sample (first 5): {original_embedding[:5]}")

    # Step 2: Test with exact embedding (should be #1 result)
    print("\n" + "=" * 70)
    print("STEP 2: Testing with EXACT embedding (identity match)...")
    print("=" * 70)

    exact_query = """
        SELECT META().id, repo_id, file_path, content, `language`,
               SEARCH_SCORE() as score
        FROM `code_kosha`
        WHERE type = 'code_chunk'
        AND SEARCH(`code_kosha`, {
            "knn": [{
                "field": "embedding",
                "vector": $vector,
                "k": 10
            }]
        })
        ORDER BY score DESC
        LIMIT 10
    """

    params = {"vector": original_embedding}
    result = db.cluster.query(exact_query, QueryOptions(named_parameters=params))

    results = list(result)
    print(f"\n✓ Found {len(results)} results")

    for i, r in enumerate(results[:5], 1):
        is_match = r['id'] == chunk_id
        match_marker = " ← ORIGINAL" if is_match else ""
        score = r.get('score', 0.0)
        print(f"\n{i}. Score: {score:.6f}{match_marker}")
        print(f"   ID: {r['id']}")
        print(f"   File: {r['file_path']}")
        print(f"   Preview: {r['content'][:100]}...")

    # Check if original chunk is in top results
    original_rank = None
    for i, r in enumerate(results, 1):
        if r['id'] == chunk_id:
            original_rank = i
            break

    if original_rank:
        print(f"\n✓ Original chunk found at rank #{original_rank}")
    else:
        print(f"\n❌ Original chunk NOT found in top 10 results!")

    # Step 3: Test with jittered embedding
    print("\n" + "=" * 70)
    print("STEP 3: Testing with JITTERED embedding (+/- 0.01 noise)...")
    print("=" * 70)

    # Add small random noise to embedding
    np.random.seed(42)
    jitter = np.random.normal(0, 0.01, len(original_embedding))
    jittered_embedding = [float(x + j) for x, j in zip(original_embedding, jitter)]

    print(f"Jitter applied: mean={np.mean(jitter):.6f}, std={np.std(jitter):.6f}")
    print(f"Original embedding sample: {original_embedding[:5]}")
    print(f"Jittered embedding sample: {jittered_embedding[:5]}")

    params_jitter = {"vector": jittered_embedding}
    result = db.cluster.query(exact_query, QueryOptions(named_parameters=params_jitter))

    results_jitter = list(result)
    print(f"\n✓ Found {len(results_jitter)} results")

    for i, r in enumerate(results_jitter[:5], 1):
        is_match = r['id'] == chunk_id
        match_marker = " ← ORIGINAL" if is_match else ""
        score = r.get('score', 0.0)
        print(f"\n{i}. Score: {score:.6f}{match_marker}")
        print(f"   ID: {r['id']}")
        print(f"   File: {r['file_path']}")
        print(f"   Preview: {r['content'][:100]}...")

    # Check if original chunk is in top results
    jittered_rank = None
    for i, r in enumerate(results_jitter, 1):
        if r['id'] == chunk_id:
            jittered_rank = i
            break

    if jittered_rank:
        print(f"\n✓ Original chunk found at rank #{jittered_rank} with jittered embedding")
    else:
        print(f"\n❌ Original chunk NOT found in top 10 results with jittered embedding!")

    # Step 4: Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Exact embedding match: rank #{original_rank if original_rank else 'NOT FOUND'}")
    print(f"Jittered embedding match: rank #{jittered_rank if jittered_rank else 'NOT FOUND'}")

    if original_rank == 1:
        print("✓ Vector search is working correctly (exact match at rank 1)")
    elif original_rank:
        print(f"⚠️  Vector search working but not optimal (exact match at rank {original_rank})")
    else:
        print("❌ Vector search BROKEN (can't even find exact embedding match)")

    if results and all(r['score'] == 0.0 for r in results):
        print("\n❌ CRITICAL: All scores are 0.0 - vector similarity not calculating!")

    print("=" * 70)

if __name__ == '__main__':
    asyncio.run(test_identity_match())
