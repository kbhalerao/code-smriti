#!/usr/bin/env python3
"""
Check what's actually in the documents that FTS claims match "job_counter"
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient

async def main():
    print("=" * 80)
    print("CHECKING DOCUMENT CONTENT - FTS vs Bucket")
    print("=" * 80)

    # First, get FTS hits for "job_counter"
    fts_request = {
        "query": {
            "match": "job_counter",
            "field": "content"
        },
        "size": 5
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

        print(f"\nFTS found {len(hits)} hits for 'job_counter'")
        print("\nChecking what's actually in these documents...\n")

        db = CouchbaseClient()

        for i, hit in enumerate(hits[:3], 1):
            doc_id = hit['id']
            score = hit.get('score', 0)

            print(f"{'-' * 80}")
            print(f"Document {i}")
            print(f"{'-' * 80}")
            print(f"ID: {doc_id}")
            print(f"FTS Score: {score:.4f}")

            # Fetch the actual document from bucket
            try:
                doc = db.bucket.default_collection().get(doc_id)
                content_obj = doc.content_as[dict]

                print(f"\nActual document fields:")
                print(f"  repo_id: {content_obj.get('repo_id', 'N/A')}")
                print(f"  file_path: {content_obj.get('file_path', 'N/A')}")
                print(f"  type: {content_obj.get('type', 'N/A')}")

                content = content_obj.get('content', content_obj.get('code_text', ''))
                print(f"  content length: {len(content)} chars")

                # Check if "job_counter" is in content
                if 'job_counter' in content.lower():
                    print(f"  ✓ 'job_counter' FOUND in content")
                    # Show snippet
                    idx = content.lower().find('job_counter')
                    snippet = content[max(0, idx-50):min(len(content), idx+100)]
                    print(f"\n  Snippet: ...{snippet}...")
                else:
                    print(f"  ✗ 'job_counter' NOT FOUND in content")
                    print(f"\n  Content preview: {content[:200]}...")

            except Exception as e:
                print(f"  ERROR fetching document: {e}")

            print()

    print("=" * 80)
    print("DIAGNOSIS:")
    print("If FTS says documents match but content doesn't contain the keyword,")
    print("then the FTS index is STALE and needs to be rebuilt.")
    print("=" * 80)

asyncio.run(main())
