#!/usr/bin/env python3
"""
Test self-retrieval: Can we retrieve a document using its own embedding?

This tests the fundamental embedding + search pipeline by:
1. Selecting a known code chunk
2. Using its own embedding to search FTS
3. Checking if we get the same chunk back
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions


async def test_self_retrieval():
    print("=" * 80)
    print("SELF-RETRIEVAL TEST")
    print("=" * 80)
    print("Testing: Can we retrieve a document using its own embedding?\n")

    db = CouchbaseClient()

    # Get a known code chunk from one of our expected files
    n1ql = """
        SELECT META().id, file_path, repo_id, embedding,
               LENGTH(content) as len, content
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND file_path LIKE '%orders/consumers.py%'
          AND repo_id = 'kbhalerao/labcore'
        LIMIT 1
    """

    result = db.cluster.query(n1ql)
    source_chunk = None
    for row in result:
        source_chunk = row
        break

    if not source_chunk:
        print("❌ Could not find test chunk")
        return

    print(f"Source chunk:")
    print(f"  File: {source_chunk['repo_id']}/{source_chunk['file_path']}")
    print(f"  ID: {source_chunk['id']}")
    print(f"  Length: {source_chunk['len']} chars")
    print(f"  Embedding dims: {len(source_chunk['embedding'])}")
    print()

    # Use the chunk's own embedding to search FTS
    embedding = source_chunk['embedding']

    fts_request = {
        "size": 20,
        "fields": ["*"],
        "knn": [{
            "field": "embedding",
            "vector": embedding,
            "k": 20
        }]
    }

    print("Searching FTS with the chunk's own embedding...")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        fts_results = response.json()
        hits = fts_results.get('hits', [])

        print(f"✓ FTS returned {len(hits)} hits\n")

        # Check results
        print("=" * 80)
        print("TOP 10 RESULTS")
        print("=" * 80)

        found_self = False
        found_same_file = 0

        # Get full documents from Couchbase
        doc_ids = [hit['id'] for hit in hits[:10]]
        n1ql = """
            SELECT META().id, repo_id, file_path, type,
                   LENGTH(content) as len
            FROM `code_kosha`
            WHERE META().id IN $doc_ids
        """

        result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))
        docs_by_id = {row['id']: row for row in result}

        for i, hit in enumerate(hits[:10], 1):
            doc_id = hit['id']
            score = hit.get('score', 0)
            doc = docs_by_id.get(doc_id, {})

            file_path = doc.get('file_path', 'unknown')
            repo_id = doc.get('repo_id', 'unknown')
            doc_len = doc.get('len', 0)

            # Check if this is the source chunk or same file
            is_self = (doc_id == source_chunk['id'])
            is_same_file = ('orders/consumers.py' in file_path and 'labcore' in repo_id)

            marker = "🎯" if is_self else ("✓" if is_same_file else " ")

            if is_self:
                found_self = True
            if is_same_file:
                found_same_file += 1

            print(f"{marker} {i}. Score: {score:.2f}")
            print(f"   {repo_id}/{file_path}")
            print(f"   Length: {doc_len} chars, Type: {doc.get('type', 'unknown')}")
            print()

        # Verdict
        print("=" * 80)
        print("VERDICT")
        print("=" * 80)

        if found_self:
            print("✅ SUCCESS: Found the source chunk itself!")
            print(f"   Also found {found_same_file - 1} other chunks from the same file")
        else:
            print("❌ FAILURE: Did NOT find the source chunk in top 10")
            if found_same_file > 0:
                print(f"   But found {found_same_file} chunks from the same file")
                print("   → Embeddings work, but exact chunk not in top results")
            else:
                print("   And found NO chunks from the same file")
                print("   → Pipeline may be broken")

        print()

        # Show what ranked highest
        if hits:
            top_hit = hits[0]
            top_doc = docs_by_id.get(top_hit['id'], {})
            print(f"Top ranked: {top_doc.get('repo_id', 'unknown')}/{top_doc.get('file_path', 'unknown')}")
            print(f"Score: {top_hit.get('score', 0):.2f}")

        print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_self_retrieval())
