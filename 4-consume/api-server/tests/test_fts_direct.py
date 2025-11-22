#!/usr/bin/env python3
"""
Test direct FTS REST API to confirm vector similarity scoring works.
This bypasses N1QL to hit the FTS endpoint directly.
"""
import asyncio
import json
import httpx
from app.database.couchbase_client import CouchbaseClient

async def test_fts_direct():
    """Test FTS REST API directly for vector search scoring"""

    print("=" * 70)
    print("TESTING DIRECT FTS REST API FOR VECTOR SIMILARITY")
    print("=" * 70)

    # Step 1: Get a code chunk with embedding (same as identity test)
    print("\nStep 1: Fetching test chunk from database...")

    db = CouchbaseClient()

    query = """
        SELECT META().id, repo_id, file_path, content, `language`, embedding
        FROM `code_kosha`
        WHERE type='code_chunk'
          AND repo_id='kbhalerao/labcore'
          AND file_path LIKE '%consumer%'
          AND LENGTH(content) > 300
        LIMIT 1
    """

    result = db.cluster.query(query)
    chunks = list(result)

    if not chunks:
        print("❌ No chunks found!")
        return

    test_chunk = chunks[0]
    chunk_id = test_chunk['id']
    original_embedding = test_chunk['embedding']

    print(f"✓ Got chunk: {chunk_id}")
    print(f"  File: {test_chunk['file_path']}")
    print(f"  Embedding dims: {len(original_embedding)}")
    print(f"  Content preview: {test_chunk['content'][:100]}...")

    # Step 2: Test FTS REST API with exact embedding (no filter)
    print("\n" + "=" * 70)
    print("Step 2: Testing FTS REST API - NO FILTER (baseline)")
    print("=" * 70)

    fts_request = {
        "knn": [{
            "field": "embedding",
            "vector": original_embedding,
            "k": 10
        }],
        "size": 10,
        "fields": ["*"]
    }

    async with httpx.AsyncClient() as client:
        print(f"\nCalling: POST http://localhost:8094/api/index/code_vector_index/query")
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

        data = response.json()
        hits = data.get('hits', [])

        print(f"\n✓ FTS returned {len(hits)} results")
        print(f"  Total hits: {data.get('total_hits', 0)}")

        # Debug: show raw first hit
        if hits:
            print(f"\n  DEBUG - Raw first hit structure:")
            print(f"  {json.dumps(hits[0], indent=4)[:500]}...")

        # Check scores
        scores = [h.get('score', 0.0) for h in hits]
        has_nonzero_scores = any(s > 0 for s in scores)

        print(f"\n  Scores: {scores[:5]}")
        print(f"  All zeros? {not has_nonzero_scores}")

        # Find original chunk
        original_rank = None
        for i, hit in enumerate(hits, 1):
            if hit.get('id') == chunk_id:
                original_rank = i
                break

        print(f"\n  Original chunk rank: #{original_rank if original_rank else 'NOT FOUND'}")

        # Show top 5
        print(f"\n  Top 5 results:")
        for i, hit in enumerate(hits[:5], 1):
            is_match = hit.get('id') == chunk_id
            marker = " ← ORIGINAL" if is_match else ""
            fields = hit.get('fields', {})
            file_path = fields.get('file_path', ['unknown'])[0] if isinstance(fields.get('file_path'), list) else fields.get('file_path', 'unknown')
            score = hit.get('score', 0.0)

            print(f"    {i}. Score: {score:.6f} | {file_path}{marker}")

    # Step 3: Test FTS REST API WITH filter (the problem case)
    print("\n" + "=" * 70)
    print("Step 3: Testing FTS REST API - WITH FILTER (repo_id)")
    print("=" * 70)

    fts_request_filtered = {
        "knn": [{
            "field": "embedding",
            "vector": original_embedding,
            "k": 10,
            "filter": {
                "conjuncts": [
                    {"term": "code_chunk", "field": "type"},
                    {"term": "kbhalerao/labcore", "field": "repo_id"}
                ]
            }
        }],
        "size": 10,
        "fields": ["*"]
    }

    async with httpx.AsyncClient() as client:
        print(f"\nCalling FTS with filter...")
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request_filtered,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        if response.status_code != 200:
            print(f"❌ FTS filtered request failed: {response.status_code}")
            print(response.text[:500])
            return

        data = response.json()
        hits = data.get('hits', [])

        print(f"\n✓ FTS returned {len(hits)} results with filter")
        print(f"  Total hits: {data.get('total_hits', 0)}")

        # Check scores
        scores = [h.get('score', 0.0) for h in hits]
        has_nonzero_scores = any(s > 0 for s in scores)

        print(f"\n  Scores: {scores[:5]}")
        print(f"  All zeros? {not has_nonzero_scores}")

        # Find original chunk
        original_rank_filtered = None
        for i, hit in enumerate(hits, 1):
            if hit.get('id') == chunk_id:
                original_rank_filtered = i
                break

        print(f"\n  Original chunk rank: #{original_rank_filtered if original_rank_filtered else 'NOT FOUND'}")

        # Check repo filtering
        repos = set()
        for hit in hits:
            fields = hit.get('fields', {})
            repo = fields.get('repo_id', ['unknown'])[0] if isinstance(fields.get('repo_id'), list) else fields.get('repo_id', 'unknown')
            repos.add(repo)

        print(f"\n  Unique repos in results: {repos}")
        filter_working = repos == {'kbhalerao/labcore'}
        print(f"  Filter working correctly? {filter_working}")

        # Show top 5
        print(f"\n  Top 5 results:")
        for i, hit in enumerate(hits[:5], 1):
            is_match = hit.get('id') == chunk_id
            marker = " ← ORIGINAL" if is_match else ""
            fields = hit.get('fields', {})
            file_path = fields.get('file_path', ['unknown'])[0] if isinstance(fields.get('file_path'), list) else fields.get('file_path', 'unknown')
            repo = fields.get('repo_id', ['unknown'])[0] if isinstance(fields.get('repo_id'), list) else fields.get('repo_id', 'unknown')
            score = hit.get('score', 0.0)

            print(f"    {i}. Score: {score:.6f} | {repo}/{file_path}{marker}")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"FTS without filter:")
    print(f"  - Returns scores? {has_nonzero_scores}")
    print(f"  - Original at rank: #{original_rank if original_rank else 'NOT FOUND'}")
    print(f"\nFTS with filter:")
    print(f"  - Returns scores? {has_nonzero_scores}")
    print(f"  - Filter works? {filter_working}")
    print(f"  - Original at rank: #{original_rank_filtered if original_rank_filtered else 'NOT FOUND'}")

    if has_nonzero_scores and original_rank == 1:
        print(f"\n✓ FTS REST API works correctly for vector search!")
    else:
        print(f"\n❌ FTS REST API also has issues")

    print("=" * 70)

if __name__ == '__main__':
    asyncio.run(test_fts_direct())
