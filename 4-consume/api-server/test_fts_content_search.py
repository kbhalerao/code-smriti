#!/usr/bin/env python3
"""
Test if FTS text search is actually working on the content field.
"""
import asyncio
import httpx

async def test_direct_fts():
    """Test FTS directly to see if content field is searchable"""

    print("=" * 70)
    print("TESTING FTS CONTENT FIELD DIRECTLY")
    print("=" * 70)

    # Test 1: Search for a very specific term that should exist
    test_cases = [
        {
            "term": "job_counter",
            "description": "Specific decorator name"
        },
        {
            "term": "SyncConsumer",
            "description": "Django Channels class"
        },
        {
            "term": "$state",
            "description": "Svelte 5 rune"
        },
        {
            "term": "redis",
            "description": "Redis keyword"
        }
    ]

    async with httpx.AsyncClient() as client:
        for test in test_cases:
            term = test["term"]
            print(f"\nTest: {test['description']}")
            print(f"Searching for: '{term}'")
            print("-" * 70)

            # Direct FTS query
            fts_request = {
                "query": {
                    "match": term,
                    "field": "content"
                },
                "size": 5
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

            print(f"Total matches: {total}")
            print(f"Top hits returned: {len(hits)}")

            if hits:
                print("\nTop 3 results:")
                for i, hit in enumerate(hits[:3], 1):
                    doc_id = hit.get('id', 'unknown')
                    score = hit.get('score', 0.0)
                    print(f"  {i}. {doc_id[:80]}... (score: {score:.4f})")
            else:
                print("⚠️  NO RESULTS FOUND - Text search may not be working!")
            print()

asyncio.run(test_direct_fts())
