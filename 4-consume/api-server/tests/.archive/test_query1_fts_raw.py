#!/usr/bin/env python3
"""
Diagnostic: Check what FTS returns for Query 1 BEFORE N1QL filtering
"""
import asyncio
import httpx
from sentence_transformers import SentenceTransformer
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions

async def main():
    print("=" * 80)
    print("DIAGNOSTIC: Query 1 FTS Raw Results")
    print("=" * 80)

    # Load embedding model
    print("\nLoading embedding model...")
    model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)

    # Query 1 parameters
    vector_query = "Django Channels background worker with job counter decorator"
    text_query = "job_counter SyncConsumer decorator background worker"

    # Generate embedding
    query_with_prefix = f"search_document: {vector_query}"
    query_embedding = model.encode(query_with_prefix).tolist()
    print(f"✓ Generated embedding for: '{vector_query}'")

    # Build FTS request (hybrid mode)
    filter_conjuncts = [
        {"term": "code_chunk", "field": "type"},
        {"match": text_query, "field": "content"}
    ]

    fts_request = {
        "size": 20,
        "fields": ["*"],
        "knn": [{
            "field": "embedding",
            "vector": query_embedding,
            "k": 20,
            "filter": {"conjuncts": filter_conjuncts}
        }]
    }

    print("\nFTS Request:")
    print(f"  Vector query: {vector_query}")
    print(f"  Text query: {text_query}")
    print(f"  Type filter: code_chunk")
    print(f"  Limit: 20")

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

        print(f"\nFTS returned: {len(hits)} hits (total: {total})")

        if not hits:
            print("  ⚠️  No results from FTS!")
            return

        doc_ids = [hit['id'] for hit in hits]
        print(f"\nFirst 5 doc IDs:")
        for i, doc_id in enumerate(doc_ids[:5], 1):
            print(f"  {i}. {doc_id}")

        # Check actual documents in database
        print("\n" + "=" * 80)
        print("CHECKING ACTUAL DOCUMENTS IN DATABASE")
        print("=" * 80)

        db = CouchbaseClient()
        n1ql = """
            SELECT META().id, repo_id, file_path, type, LENGTH(content) as content_len
            FROM `code_kosha`
            WHERE META().id IN $doc_ids
        """

        result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))

        type_counts = {"code_chunk": 0, "document": 0, "commit": 0, "other": 0}
        init_count = 0
        missing_count = 0
        found_docs = []

        for row in result:
            found_docs.append(row)
            doc_type = row.get('type', 'unknown')
            if doc_type in type_counts:
                type_counts[doc_type] += 1
            else:
                type_counts["other"] += 1

            if '__init__.py' in row.get('file_path', ''):
                init_count += 1

        missing_count = len(hits) - len(found_docs)

        print(f"\nResults breakdown:")
        print(f"  Found in DB: {len(found_docs)}/{len(hits)}")
        if missing_count > 0:
            print(f"  ⚠️  Missing/Stale: {missing_count}")

        print(f"\nType distribution:")
        for doc_type, count in type_counts.items():
            if count > 0:
                symbol = "✓" if doc_type == "code_chunk" else "✗"
                print(f"  {symbol} {doc_type}: {count}")

        print(f"\n__init__.py files: {init_count}")

        print("\n" + "=" * 80)
        print("SAMPLE DOCUMENTS (first 5):")
        print("=" * 80)

        for i, row in enumerate(found_docs[:5], 1):
            is_code = row.get('type') == 'code_chunk'
            is_init = '__init__.py' in row.get('file_path', '')
            type_status = '✓ PASS' if is_code else '✗ FAIL (wrong type)'
            init_status = '✗ FAIL (__init__)' if is_init else '✓ PASS'

            print(f"\n{i}. {row.get('file_path', 'UNKNOWN')}")
            print(f"   Repo: {row.get('repo_id', 'UNKNOWN')}")
            print(f"   Type: {row.get('type', 'UNKNOWN')} {type_status}")
            print(f"   Content length: {row.get('content_len', 0)} chars")
            print(f"   Init filter: {init_status}")

        # Summary
        print("\n" + "=" * 80)
        print("FILTERING ANALYSIS")
        print("=" * 80)

        code_chunks = type_counts["code_chunk"]
        non_code = type_counts["document"] + type_counts["commit"] + type_counts["other"]

        print(f"After type filter (code_chunk only): {code_chunks}/{len(found_docs)} would pass")
        print(f"After __init__.py filter: {code_chunks - init_count}/{len(found_docs)} would pass")

        if code_chunks - init_count == 0:
            print("\n⚠️  ALL RESULTS FILTERED OUT!")
            print("Reason: All code_chunk results are __init__.py files")

asyncio.run(main())
