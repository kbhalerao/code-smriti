#!/usr/bin/env python3
"""Debug why FTS filtering is inconsistent"""
import asyncio
import httpx
import os
from sentence_transformers import SentenceTransformer


async def debug_fts():
    """Check what FTS is actually returning"""

    embedding_model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
    query_embedding = embedding_model.encode(
        "search_document: Django background task processing",
        normalize_embeddings=True
    ).tolist()

    # FTS request with type filter
    fts_request = {
        "size": 10,  # Smaller for debugging
        "fields": ["type", "repo_id", "file_path"],  # Request these fields
        "knn": [{
            "field": "embedding",
            "vector": query_embedding,
            "k": 10,
            "filter": {
                "term": "code_chunk",
                "field": "type"
            }
        }]
    }

    fts_host = os.getenv("COUCHBASE_HOST", "localhost")
    fts_url = f"http://{fts_host}:8094/api/index/code_vector_index/query"

    async with httpx.AsyncClient() as client:
        response = await client.post(
            fts_url,
            json=fts_request,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        fts_results = response.json()
        hits = fts_results.get('hits', [])

        print("=" * 80)
        print("FTS RESPONSE ANALYSIS")
        print("=" * 80)
        print(f"\nReturned {len(hits)} hits")
        print(f"\nChecking if 'type' field is returned in FTS response...")

        for i, hit in enumerate(hits[:5], 1):
            print(f"\nHit #{i}:")
            print(f"  ID: {hit.get('id')}")
            print(f"  Score: {hit.get('score', 0):.3f}")
            print(f"  Fields in response: {list(hit.get('fields', {}).keys())}")

            # Check if type is in fields
            fields = hit.get('fields', {})
            if 'type' in fields:
                print(f"  ✅ Type in FTS response: {fields['type']}")
            else:
                print(f"  ❌ Type NOT in FTS response (need to fetch from N1QL)")

        # Now check via N1QL
        from app.database.couchbase_client import CouchbaseClient
        from couchbase.options import QueryOptions

        db = CouchbaseClient()
        doc_ids = [hit['id'] for hit in hits]

        n1ql = """
            SELECT META().id, type, repo_id, file_path
            FROM `code_kosha`
            WHERE META().id IN $doc_ids
        """

        result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))

        print(f"\n{'='*80}")
        print("N1QL VERIFICATION")
        print("="*80)

        type_counts = {}
        for row in result:
            doc_type = row.get('type', 'NULL')
            type_counts[doc_type] = type_counts.get(doc_type, 0) + 1

        print(f"\nTypes from N1QL (actual in database):")
        for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            marker = "✅" if t == "code_chunk" else "❌"
            print(f"  {marker} {t}: {count}")

        if len(type_counts) > 1 or 'code_chunk' not in type_counts:
            print(f"\n❌ FTS FILTER IS NOT WORKING!")
            print(f"   Requested: only 'code_chunk'")
            print(f"   Got: {list(type_counts.keys())}")
        else:
            print(f"\n✅ FTS filter working correctly - all results are 'code_chunk'")


if __name__ == "__main__":
    asyncio.run(debug_fts())
