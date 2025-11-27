#!/usr/bin/env python3
"""Test how many documents pass through FTS vs N1QL filtering"""
import asyncio
import httpx
import os


async def test_filter_efficiency():
    """Compare FTS results before and after N1QL filtering"""

    query = "Django model with custom fields"
    doc_type = "code_chunk"

    print("=" * 80)
    print("FILTER EFFICIENCY TEST")
    print("=" * 80)
    print(f"Query: {query}")
    print(f"Doc type: {doc_type}")
    print()

    # Step 1: Call FTS directly to see raw results
    from sentence_transformers import SentenceTransformer

    embedding_model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
    query_embedding = embedding_model.encode(
        f"search_document: {query}",
        normalize_embeddings=True
    ).tolist()

    # FTS request with type filter
    fts_request = {
        "size": 50,
        "fields": ["*"],
        "knn": [{
            "field": "embedding",
            "vector": query_embedding,
            "k": 50,
            "filter": {
                "term": doc_type,
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

        print(f"FTS returned: {len(hits)} hits")

        # Check what types we actually got
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

        # Analyze types
        type_counts = {}
        matching_type = 0

        for row in result:
            row_type = row.get('type', 'unknown')
            type_counts[row_type] = type_counts.get(row_type, 0) + 1
            if row_type == doc_type:
                matching_type += 1

        print(f"\nTypes in FTS results:")
        for t, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            marker = "✅" if t == doc_type else "❌"
            print(f"  {marker} {t}: {count}")

        print(f"\n{'='*80}")
        print("FILTER EFFECTIVENESS")
        print("="*80)
        print(f"FTS returned:        {len(hits)} documents")
        print(f"Matching type:       {matching_type} ({matching_type/len(hits)*100:.1f}%)")
        print(f"Wrong type filtered: {len(hits) - matching_type} ({(len(hits)-matching_type)/len(hits)*100:.1f}%)")

        # Calculate if we need 10x oversampling
        if matching_type > 0:
            effective_factor = len(hits) / matching_type
            print(f"\nEffective oversample needed: {effective_factor:.1f}x")
            print(f"Current oversample factor:   10x")

            if effective_factor < 2:
                print("\n💡 FTS filtering is working well! Could reduce oversample factor to 2-3x")
            elif effective_factor < 5:
                print("\n✅ FTS filtering is okay. Current 10x is conservative but reasonable")
            else:
                print(f"\n⚠️  FTS filtering is broken. Need {effective_factor:.0f}x oversampling")


if __name__ == "__main__":
    asyncio.run(test_filter_efficiency())
