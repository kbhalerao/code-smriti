#!/usr/bin/env python3
"""
Test using text search as a REQUIRED filter (must match) combined with vector ranking.

This should eliminate empty files that have no text matches.
"""
import asyncio
import httpx

async def test_text_filter_hybrid():
    """Test hybrid search with text as required filter"""

    print("=" * 70)
    print("TESTING: Text Search as REQUIRED FILTER + Vector Ranking")
    print("=" * 70)

    test_cases = [
        {
            "name": "Django Channels job counter",
            "vector_query": "Django Channels background worker with job counter decorator",
            "text_filter": "job_counter",
            "expected": "Should find consumer_decorators.py"
        },
        {
            "name": "Svelte runes",
            "vector_query": "Svelte 5 component with runes for state management",
            "text_filter": "$state",
            "expected": "Should find ChatInput.svelte"
        },
        {
            "name": "Redis tracking",
            "vector_query": "Redis integration for background job tracking",
            "text_filter": "redis",
            "expected": "Should find redis_lock.py or consumer_decorators.py"
        }
    ]

    from sentence_transformers import SentenceTransformer
    embedding_model = SentenceTransformer(
        "nomic-ai/nomic-embed-text-v1.5",
        trust_remote_code=True
    )

    async with httpx.AsyncClient() as client:
        for test in test_cases:
            print(f"\n{'#' * 70}")
            print(f"TEST: {test['name']}")
            print(f"{'#' * 70}")
            print(f"Vector query: {test['vector_query']}")
            print(f"Text filter (MUST match): '{test['text_filter']}'")
            print(f"Expected: {test['expected']}")
            print()

            # Generate embedding
            query_with_prefix = f"search_document: {test['vector_query']}"
            query_embedding = embedding_model.encode(query_with_prefix).tolist()

            # FTS request with text as CONJUNCT (required filter) + vector ranking
            fts_request = {
                "query": {
                    "conjuncts": [
                        {
                            "match": test['text_filter'],
                            "field": "content"
                        },
                        {
                            "term": "code_chunk",
                            "field": "type"
                        }
                    ]
                },
                "knn": [{
                    "field": "embedding",
                    "vector": query_embedding,
                    "k": 10
                }],
                "size": 10
            }

            response = await client.post(
                "http://localhost:8094/api/index/code_vector_index/query",
                json=fts_request,
                auth=("Administrator", "password123"),
                timeout=30.0
            )

            if response.status_code != 200:
                print(f"❌ FTS request failed: {response.status_code}")
                print(response.text)
                continue

            results = response.json()
            hits = results.get('hits', [])
            total = results.get('total_hits', 0)

            print(f"Results: {len(hits)} hits (total: {total})")
            print()

            if not hits:
                print("⚠️  NO RESULTS - Text filter may be too strict or no matches exist")
            else:
                print("Top 5 results:")
                for i, hit in enumerate(hits[:5], 1):
                    doc_id = hit.get('id', 'unknown')
                    score = hit.get('score', 0.0)
                    print(f"  {i}. {doc_id[:80]}...")
                    print(f"     Score: {score:.4f}")

            print()

    print("=" * 70)
    print("CONCLUSION:")
    print("If results look better, update search_code_tool to use text as filter")
    print("=" * 70)

asyncio.run(test_text_filter_hybrid())
