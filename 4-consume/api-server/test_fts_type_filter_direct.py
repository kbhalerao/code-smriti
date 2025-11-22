#!/usr/bin/env python3
"""
Test FTS type filter directly against the FTS REST API
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions

async def main():
    print("=" * 80)
    print("TESTING: FTS Type Filter Directly")
    print("=" * 80)

    # Test 1: FTS text search with type filter
    print("\nTest 1: Text search with type='code_chunk' filter")
    print("-" * 80)

    fts_request = {
        "size": 5,
        "fields": ["*"],
        "query": {
            "conjuncts": [
                {"term": "code_chunk", "field": "type"},
                {"match": "job_counter", "field": "content"}
            ]
        }
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        fts_results = response.json()
        hits = fts_results.get('hits', [])
        total = fts_results.get('total_hits', 0)

        print(f"FTS returned: {len(hits)} hits (total: {total})")

        if not hits:
            print("  No results!")
        else:
            doc_ids = [hit['id'] for hit in hits]
            print(f"\nDocument IDs: {doc_ids[:3]}")

            # Now check actual types in database
            print("\nChecking actual document types in database:")
            print("-" * 80)

            db = CouchbaseClient()
            n1ql = """
                SELECT META().id, repo_id, file_path, type
                FROM `code_kosha`
                WHERE META().id IN $doc_ids
            """

            result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))
            for row in result:
                print(f"  {row['file_path']}")
                print(f"    Type: {row['type']} {'✓ CORRECT' if row['type'] == 'code_chunk' else '✗ WRONG TYPE!'}")

    print("\n" + "=" * 80)
    print("If we see type='document' results, the FTS filter is BROKEN")
    print("=" * 80)

asyncio.run(main())
