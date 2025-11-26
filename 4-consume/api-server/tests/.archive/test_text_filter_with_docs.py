#!/usr/bin/env python3
"""
Test text-as-filter approach and fetch full documents to see file paths.
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions

async def main():
    print("=" * 80)
    print("TEXT-AS-FILTER HYBRID SEARCH - WITH FILE PATHS")
    print("=" * 80)

    test_cases = [
        {
            "name": "Django Channels job counter",
            "vector_query": "Django Channels background worker with job counter decorator",
            "text_filter": "job_counter",
            "expected_files": ["orders/consumers.py", "common/consumer_decorators.py"]
        },
        {
            "name": "Svelte runes",
            "vector_query": "Svelte 5 component with runes for state management",
            "text_filter": "$state",
            "expected_files": ["src/lib/components/chat/ChatInput.svelte"]
        },
        {
            "name": "Redis tracking",
            "vector_query": "Redis integration for background job tracking",
            "text_filter": "redis",
            "expected_files": ["common/consumer_decorators.py", "common/redis_lock.py"]
        }
    ]

    from sentence_transformers import SentenceTransformer
    embedding_model = SentenceTransformer(
        "nomic-ai/nomic-embed-text-v1.5",
        trust_remote_code=True
    )

    db = CouchbaseClient()

    total_matches = 0
    total_expected = 0

    for test in test_cases:
        print(f"\n{'#' * 80}")
        print(f"TEST: {test['name']}")
        print(f"{'#' * 80}")
        print(f"Text filter (MUST match): '{test['text_filter']}'")
        print(f"Expected files: {', '.join(test['expected_files'])}")
        print()

        # Generate embedding
        query_with_prefix = f"search_document: {test['vector_query']}"
        query_embedding = embedding_model.encode(query_with_prefix).tolist()

        # FTS request with text as CONJUNCT (required filter)
        fts_request = {
            "query": {
                "conjuncts": [
                    {
                        "match": test['text_filter'],
                        "field": "content"
                    },
                    {
                        "term": "code_chunk",
                        "field": "type"
                    }
                ]
            },
            "knn": [{
                "field": "embedding",
                "vector": query_embedding,
                "k": 10
            }],
            "size": 10
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "http://localhost:8094/api/index/code_vector_index/query",
                json=fts_request,
                auth=("Administrator", "password123"),
                timeout=30.0
            )

            if response.status_code != 200:
                print(f"❌ FTS failed: {response.status_code}")
                continue

            fts_results = response.json()
            hits = fts_results.get('hits', [])

            if not hits:
                print("⚠️  NO RESULTS")
                continue

            # Fetch full documents
            doc_ids = [hit['id'] for hit in hits]
            scores_by_id = {hit['id']: hit.get('score', 0.0) for hit in hits}

            n1ql = """
                SELECT META().id, repo_id, file_path, content, `language`
                FROM `code_kosha`
                WHERE META().id IN $doc_ids AND type = 'code_chunk'
            """

            result = db.cluster.query(n1ql, QueryOptions(named_parameters={"doc_ids": doc_ids}))

            docs = []
            for row in result:
                doc_id = row['id']
                docs.append({
                    "repo_id": row.get('repo_id', ''),
                    "file_path": row.get('file_path', ''),
                    "score": scores_by_id.get(doc_id, 0.0),
                    "content_preview": row.get('content', '')[:100]
                })

            # Sort by score
            docs.sort(key=lambda x: x['score'], reverse=True)

            print(f"Results: {len(docs)} documents")
            print()

            # Check matches
            matches = []
            for i, doc in enumerate(docs[:10], 1):
                file_path = doc['file_path']
                is_match = any(exp in file_path for exp in test['expected_files'])
                marker = "✓" if is_match else " "

                if is_match:
                    matches.append(file_path)

                print(f"{marker} {i}. {doc['repo_id']}/{file_path}")
                print(f"   Score: {doc['score']:.2f}")
                if i <= 3:
                    print(f"   Preview: {doc['content_preview']}...")
                print()

            total_expected += len(test['expected_files'])
            total_matches += len(matches)

            match_rate = len(matches) / len(test['expected_files']) * 100 if test['expected_files'] else 0
            print(f"{'=' * 80}")
            print(f"MATCH RATE: {len(matches)}/{len(test['expected_files'])} ({match_rate:.0f}%)")
            if matches:
                print(f"Matched: {', '.join(matches)}")
            print(f"{'=' * 80}")

    # Overall summary
    print(f"\n{'=' * 80}")
    print("OVERALL SUMMARY")
    print(f"{'=' * 80}")
    overall_precision = total_matches / total_expected * 100 if total_expected else 0
    print(f"Total: {total_matches}/{total_expected} ({overall_precision:.1f}%)")

    if overall_precision >= 50:
        print("\n✓ TEXT-AS-FILTER APPROACH IS WORKING!")
        print("  This eliminates empty files and improves precision.")
        print("  Next step: Update search_code_tool to use this approach.")
    else:
        print("\n⚠️  Still need more tuning...")

    print(f"{'=' * 80}")

asyncio.run(main())
