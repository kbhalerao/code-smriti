#!/usr/bin/env python3
"""
Test kNN vector search with type pre-filtering
"""
import asyncio
import httpx
from sentence_transformers import SentenceTransformer
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions

async def main():
    print("=" * 80)
    print("TESTING: Vector kNN Search with Type Pre-filtering")
    print("=" * 80)

    # Load embedding model
    print("\nLoading embedding model...")
    model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)

    # Generate query embedding
    query = "search_document: Django Channels background worker"
    query_embedding = model.encode(query).tolist()
    print(f"✓ Generated embedding for: '{query}'")

    # Test 1: kNN search WITH type pre-filter
    print("\nTest 1: Vector kNN with type='code_chunk' pre-filter")
    print("-" * 80)

    fts_request = {
        "size": 5,
        "fields": ["*"],
        "knn": [{
            "field": "embedding",
            "vector": query_embedding,
            "k": 5,
            "filter": {
                "term": "code_chunk",
                "field": "type"
            }
        }]
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

            # Check actual types in database
            print("\nChecking actual document types in database:")
            print("-" * 80)

            db = CouchbaseClient()
            n1ql = """
                SELECT META().id, repo_id, file_path, type
                FROM `code_kosha`
                WHERE META().id IN $doc_ids
            """

            result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))
            wrong_type_count = 0
            found_count = 0
            for row in result:
                found_count += 1
                is_correct = row.get('type') == 'code_chunk'
                status = '✓ CORRECT' if is_correct else '✗ WRONG TYPE!'
                if not is_correct:
                    wrong_type_count += 1
                print(f"  {row.get('file_path', 'UNKNOWN')}")
                print(f"    Type: {row.get('type', 'MISSING')} {status}")

            print(f"\nResults: {found_count}/{len(hits)} docs found in DB")
            if found_count < len(hits):
                print(f"⚠️  FTS has {len(hits) - found_count} stale document IDs")
            print(f"Types: {found_count - wrong_type_count}/{found_count} correct type")

    print("\n" + "=" * 80)
    print("If we see type='document' results, kNN pre-filtering is BROKEN")
    print("=" * 80)

asyncio.run(main())
