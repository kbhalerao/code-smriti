#!/usr/bin/env python3
"""
Check if expected files are in top 100 FTS results for Query 1
"""
import asyncio
import httpx
from sentence_transformers import SentenceTransformer

async def main():
    print("=" * 80)
    print("CHECK: Are expected files in top 100 FTS results?")
    print("=" * 80)

    # Load model
    model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)

    # Query 1 parameters (exact same as test)
    vector_query = "Django Channels background worker with job counter decorator"
    text_query = "job_counter SyncConsumer decorator background worker"

    # Generate embedding
    query_embedding = model.encode(f"search_document: {vector_query}").tolist()

    # Build FTS request (hybrid mode, 100 results)
    filter_conjuncts = [
        {"term": "code_chunk", "field": "type"},
        {"match": text_query, "field": "content"}
    ]

    fts_request = {
        "size": 100,
        "fields": ["*"],
        "knn": [{
            "field": "embedding",
            "vector": query_embedding,
            "k": 100,
            "filter": {"conjuncts": filter_conjuncts}
        }]
    }

    print(f"\nQuery: {vector_query}")
    print(f"Text:  {text_query}")
    print(f"\nExpected files:")
    print("  - orders/consumers.py")
    print("  - common/consumer_decorators.py")

    # Call FTS
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

        print(f"\n✓ FTS returned {len(hits)} hits (total: {total})")

        # Check for expected files
        print("\n" + "=" * 80)
        print("SEARCHING FOR EXPECTED FILES IN RESULTS")
        print("=" * 80)

        found_orders = []
        found_decorators = []
        found_init = 0
        found_background = []

        for i, hit in enumerate(hits):
            doc_id = hit['id']
            score = hit.get('score', 0)

            # We can't see file_path in FTS results (not stored)
            # But we can check document IDs

            # Just count patterns for now
            if i < 20:  # Show first 20
                print(f"{i+1:3d}. ID: {doc_id[:60]:<60} Score: {score:.2f}")

        print(f"\nOut of {len(hits)} results:")
        print(f"  (Cannot determine file paths - 'file_path' not stored in FTS)")
        print(f"  Need to fetch from Couchbase to verify")

        # Now fetch from Couchbase
        from app.database.couchbase_client import CouchbaseClient
        from couchbase.options import QueryOptions

        db = CouchbaseClient()
        doc_ids = [hit['id'] for hit in hits]

        n1ql = """
            SELECT META().id, repo_id, file_path, type
            FROM `code_kosha`
            WHERE META().id IN $doc_ids
        """

        print("\n" + "=" * 80)
        print("CHECKING FILE PATHS IN COUCHBASE")
        print("=" * 80)

        result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))

        for row in result:
            file_path = row.get('file_path', '')
            repo_id = row.get('repo_id', '')

            if 'orders/consumers.py' in file_path and 'labcore' in repo_id:
                found_orders.append((file_path, repo_id))
            elif 'common/consumer_decorators.py' in file_path and 'labcore' in repo_id:
                found_decorators.append((file_path, repo_id))
            elif '__init__.py' in file_path:
                found_init += 1
            elif 'backgroundconsumers.py' in file_path:
                found_background.append((file_path, repo_id))

        print(f"\nFound in top 100 FTS results:")
        print(f"  ✓ orders/consumers.py (kbhalerao/labcore): {len(found_orders)} chunks")
        if found_orders:
            for fp, repo in found_orders[:3]:
                print(f"      - {repo}/{fp}")

        print(f"  ✓ common/consumer_decorators.py (kbhalerao/labcore): {len(found_decorators)} chunks")
        if found_decorators:
            for fp, repo in found_decorators[:3]:
                print(f"      - {repo}/{fp}")

        print(f"\n  ✗ __init__.py files: {found_init} chunks")
        print(f"  ~ backgroundconsumers.py (similar but not expected): {len(found_background)} chunks")

        # Verdict
        print("\n" + "=" * 80)
        print("VERDICT")
        print("=" * 80)

        if len(found_orders) > 0 and len(found_decorators) > 0:
            print("\n✓ EXPECTED FILES ARE IN TOP 100 FTS RESULTS")
            print("  Problem: They're being filtered out by N1QL __init__.py exclusion")
            print("  OR: They're ranking too low (below position 100)")
        elif len(found_orders) == 0 or len(found_decorators) == 0:
            print("\n✗ EXPECTED FILES NOT IN TOP 100 FTS RESULTS")
            print("  Problem: FTS hybrid scoring is broken")
            print("  Likely cause:")
            print("    - BM25 text search dominates vector search")
            print("    - Expected files don't match text query well")
            print("    - Need to adjust hybrid scoring weights")

        if found_init > len(found_orders) + len(found_decorators):
            print(f"\n⚠️  More __init__.py files ({found_init}) than expected files in results")
            print("  Confirms __init__.py pollution problem")

asyncio.run(main())
