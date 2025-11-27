#!/usr/bin/env python3
"""
Test if FTS type filtering actually works
"""
import asyncio
import httpx

async def main():
    print("=" * 80)
    print("TESTING: FTS Type Filter")
    print("=" * 80)

    # Test 1: Text search WITHOUT type filter
    print("\nTest 1: Text search for 'job_counter' (NO type filter)")
    print("-" * 80)

    fts_request1 = {
        "query": {
            "match": "job_counter",
            "field": "content"
        },
        "size": 5
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request1,
            auth=("Administrator", "password123"),
            timeout=30.0
        )
        results = response.json()
        hits = results.get('hits', [])
        print(f"Results: {len(hits)} hits")

    # Test 2: Text search WITH type filter using conjuncts
    print("\nTest 2: Same search WITH type='code_chunk' filter (conjuncts)")
    print("-" * 80)

    fts_request2 = {
        "query": {
            "conjuncts": [
                {
                    "match": "job_counter",
                    "field": "content"
                },
                {
                    "term": "code_chunk",
                    "field": "type"
                }
            ]
        },
        "size": 5
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request2,
            auth=("Administrator", "password123"),
            timeout=30.0
        )
        results = response.json()
        hits = results.get('hits', [])
        print(f"Results: {len(hits)} hits")

        if hits:
            print("\nFirst result ID:")
            print(f"  {hits[0]['id']}")

    # Test 3: JUST type filter
    print("\nTest 3: JUST type filter (no text search)")
    print("-" * 80)

    fts_request3 = {
        "query": {
            "term": "code_chunk",
            "field": "type"
        },
        "size": 5
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request3,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        if response.status_code != 200:
            print(f"ERROR: {response.status_code}")
            print(response.text)
        else:
            results = response.json()
            hits = results.get('hits', [])
            total = results.get('total_hits', 0)
            print(f"Results: {len(hits)} hits (total: {total})")

    print("\n" + "=" * 80)
    print("DIAGNOSIS:")
    print("If Test 3 fails, the 'type' field is NOT indexed for term queries")
    print("  → Need to update FTS index definition to index 'type' field")
    print("=" * 80)

asyncio.run(main())
