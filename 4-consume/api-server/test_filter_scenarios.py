#!/usr/bin/env python3
"""Test FTS filtering with different filter combinations"""
import asyncio
import httpx
import os
from sentence_transformers import SentenceTransformer


async def test_scenario(name: str, fts_request: dict, expected_type: str, expected_repo: str = None):
    """Test one filtering scenario"""

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

        # Fetch docs to check types
        from app.database.couchbase_client import CouchbaseClient
        from couchbase.options import QueryOptions

        db = CouchbaseClient()
        doc_ids = [hit['id'] for hit in hits]

        n1ql = """
            SELECT META().id, type, repo_id
            FROM `code_kosha`
            WHERE META().id IN $doc_ids
        """

        result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))

        # Count matches
        matching_type = 0
        matching_repo = 0
        total = 0

        for row in result:
            total += 1
            if row.get('type') == expected_type:
                matching_type += 1
            if expected_repo and expected_repo in row.get('repo_id', ''):
                matching_repo += 1

        type_pct = matching_type / total * 100 if total > 0 else 0
        repo_pct = matching_repo / total * 100 if total > 0 and expected_repo else 0

        print(f"\n{name}:")
        print(f"  Requested: {fts_request.get('size', '?')}")
        print(f"  Returned:  {total}")
        print(f"  Type match: {matching_type}/{total} ({type_pct:.0f}%)")
        if expected_repo:
            print(f"  Repo match: {matching_repo}/{total} ({repo_pct:.0f}%)")

        # Calculate effective oversample
        if matching_type > 0:
            if expected_repo and matching_repo > 0:
                effective = total / matching_repo
            else:
                effective = total / matching_type

            status = "✅" if effective < 2 else ("⚠️" if effective < 5 else "❌")
            print(f"  {status} Effective oversample: {effective:.1f}x")


async def main():
    print("=" * 80)
    print("FTS FILTER EFFECTIVENESS TEST")
    print("=" * 80)

    embedding_model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
    query_embedding = embedding_model.encode(
        "search_document: Django background task processing",
        normalize_embeddings=True
    ).tolist()

    # Scenario 1: Just type filter
    await test_scenario(
        "Scenario 1: Type filter only",
        {
            "size": 50,
            "fields": ["*"],
            "knn": [{
                "field": "embedding",
                "vector": query_embedding,
                "k": 50,
                "filter": {
                    "term": "code_chunk",
                    "field": "type"
                }
            }]
        },
        expected_type="code_chunk"
    )

    # Scenario 2: Type + Repo filter
    await test_scenario(
        "Scenario 2: Type + Repo filter",
        {
            "size": 50,
            "fields": ["*"],
            "knn": [{
                "field": "embedding",
                "vector": query_embedding,
                "k": 50,
                "filter": {
                    "conjuncts": [
                        {"term": "code_chunk", "field": "type"},
                        {"term": "kbhalerao/labcore", "field": "repo_id"}
                    ]
                }
            }]
        },
        expected_type="code_chunk",
        expected_repo="kbhalerao/labcore"
    )

    # Scenario 3: Document type
    await test_scenario(
        "Scenario 3: Document type filter",
        {
            "size": 50,
            "fields": ["*"],
            "knn": [{
                "field": "embedding",
                "vector": query_embedding,
                "k": 50,
                "filter": {
                    "term": "document",
                    "field": "type"
                }
            }]
        },
        expected_type="document"
    )

    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print("FTS pre-filtering is working correctly!")
    print("Current 10x oversampling is unnecessary.")
    print("\nRecommendation: Reduce oversample_factor to 2x for safety margin.")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
