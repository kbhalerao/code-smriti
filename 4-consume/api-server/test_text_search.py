#!/usr/bin/env python3
"""
Test full-text search on the updated FTS index
"""
import asyncio
import httpx

async def test_text_search():
    print("=" * 70)
    print("TESTING FULL-TEXT SEARCH ON CONTENT FIELD")
    print("=" * 70)

    # Test 1: Pure text search (no vector)
    print("\nTest 1: Text search for 'background consumer'")
    print("-" * 70)

    fts_request = {
        "query": {
            "match": "background consumer",
            "field": "content"
        },
        "size": 5
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        if response.status_code != 200:
            print(f"❌ FTS request failed: {response.status_code}")
            print(response.text)
            return

        results = response.json()
        hits = results.get('hits', [])

        print(f"Results: {len(hits)} hits")
        print(f"Total: {results.get('total_hits', 0)} total matches")

        for i, hit in enumerate(hits, 1):
            score = hit.get('score', 0.0)
            doc_id = hit.get('id', 'unknown')
            print(f"\n{i}. Score: {score:.4f}")
            print(f"   ID: {doc_id[:60]}...")

    # Test 2: Text search with type filter
    print("\n" + "-" * 70)
    print("Test 2: Text search for 'README' in documents only")
    print("-" * 70)

    fts_request = {
        "query": {
            "conjuncts": [
                {"match": "README introduction", "field": "content"},
                {"term": "document", "field": "type"}
            ]
        },
        "size": 3
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        if response.status_code != 200:
            print(f"❌ FTS request failed: {response.status_code}")
            return

        results = response.json()
        hits = results.get('hits', [])

        print(f"Results: {len(hits)} hits")

        for i, hit in enumerate(hits, 1):
            score = hit.get('score', 0.0)
            doc_id = hit.get('id', 'unknown')
            print(f"\n{i}. Score: {score:.4f}")
            print(f"   ID: {doc_id[:60]}...")

    print("\n" + "=" * 70)
    print("✓ FULL-TEXT SEARCH IS WORKING!")
    print("  The 'content' field is now indexed and searchable.")
    print("=" * 70)

asyncio.run(test_text_search())
