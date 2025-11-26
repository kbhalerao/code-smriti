#!/usr/bin/env python3
"""
Test fetching FTS results directly to see where we're losing documents.
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions

async def main():
    print("=" * 80)
    print("TESTING: FTS Results → Direct Fetch (No Filters)")
    print("=" * 80)

    # Step 1: Get FTS results for "job_counter"
    print("\nStep 1: FTS search for 'job_counter'")
    print("-" * 80)

    fts_request = {
        "query": {
            "match": "job_counter",
            "field": "content"
        },
        "size": 10
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

        print(f"FTS found: {len(hits)} hits")

        if not hits:
            print("No FTS results!")
            return

        doc_ids = [hit['id'] for hit in hits]
        print(f"\nDocument IDs returned by FTS:")
        for i, doc_id in enumerate(doc_ids[:5], 1):
            print(f"  {i}. {doc_id}")

    # Step 2: Try to fetch these documents from bucket
    print(f"\n\nStep 2: Fetch documents from bucket (NO filters)")
    print("-" * 80)

    db = CouchbaseClient()

    # Simple N1QL - just fetch by IDs, no filters
    n1ql = """
        SELECT META().id, repo_id, file_path, type,
               LENGTH(content) as content_length,
               SUBSTR(content, 0, 100) as content_preview
        FROM `code_kosha`
        WHERE META().id IN $doc_ids
    """

    result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))

    rows = list(result)
    print(f"N1QL returned: {len(rows)} documents (out of {len(doc_ids)} IDs)")
    print()

    if len(rows) < len(doc_ids):
        print(f"⚠️  LOST {len(doc_ids) - len(rows)} documents!")
        print("   This means FTS has stale IDs for documents that don't exist")
        print()

    # Step 3: Show what we got
    print("Documents fetched:")
    for i, row in enumerate(rows[:10], 1):
        print(f"\n{i}. {row.get('repo_id', 'N/A')}/{row.get('file_path', 'N/A')}")
        print(f"   Type: {row.get('type', 'N/A')}")
        print(f"   Content length: {row.get('content_length', 0)} chars")
        print(f"   Preview: {row.get('content_preview', '')}...")

    # Step 4: Now try WITH type filter (what our code does)
    print(f"\n\nStep 3: Fetch with type='code_chunk' filter (what our code does)")
    print("-" * 80)

    n1ql_filtered = """
        SELECT META().id, repo_id, file_path, type,
               LENGTH(content) as content_length
        FROM `code_kosha`
        WHERE META().id IN $doc_ids AND type = 'code_chunk'
    """

    result = db.cluster.query(n1ql_filtered, QueryOptions(named_parameters={"doc_ids": doc_ids}))
    filtered_rows = list(result)

    print(f"With type filter: {len(filtered_rows)} documents")
    print()

    if len(filtered_rows) < len(rows):
        print(f"⚠️  Type filter eliminated {len(rows) - len(filtered_rows)} documents")
        print("\nDocument types in unfiltered results:")
        type_counts = {}
        for row in rows:
            doc_type = row.get('type', 'unknown')
            type_counts[doc_type] = type_counts.get(doc_type, 0) + 1

        for doc_type, count in type_counts.items():
            print(f"  - {doc_type}: {count}")

    print("\n" + "=" * 80)
    print("DIAGNOSIS:")
    if len(rows) == len(doc_ids):
        print("✓ All FTS documents exist in bucket")
        if len(filtered_rows) < len(rows):
            print("✗ But type filter is eliminating results!")
            print("  → Fix: Ensure FTS index filters by type, or adjust filter logic")
    else:
        print("✗ FTS has stale document IDs")
        print("  → Fix: Delete and rebuild FTS index from scratch")
    print("=" * 80)

asyncio.run(main())
