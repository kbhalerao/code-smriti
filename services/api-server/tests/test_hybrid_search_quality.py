#!/usr/bin/env python3
"""
Test hybrid search quality (vector + text + filters) without LLM synthesis.

This tests raw search retrieval to see if we can get the right documents
before we even involve the RAG/LLM layer.
"""
import asyncio
import httpx
from datetime import datetime

# Test queries from eval suite with extracted keywords
TEST_QUERIES = [
    {
        "id": 1,
        "query": "Django Channels background worker with job counter decorator",
        "text_query": "job_counter SyncConsumer decorator background worker",
        "expected_files": ["orders/consumers.py", "common/consumer_decorators.py"],
        "expected_repos": ["kbhalerao/labcore"],
        "category": "framework_pattern"
    },
    {
        "id": 7,
        "query": "Svelte 5 component with runes for state management",
        "text_query": "$state $derived runes reactive ChatInput",
        "expected_files": ["src/lib/components/chat/ChatInput.svelte"],
        "expected_repos": ["your-org/chat-app"],
        "category": "ui_component"
    },
    {
        "id": 14,
        "query": "Redis integration for background job tracking",
        "text_query": "redis background job tracking decorator",
        "expected_files": ["common/consumer_decorators.py", "common/redis_lock.py"],
        "expected_repos": ["kbhalerao/labcore"],
        "category": "architecture"
    }
]

API_BASE = "http://localhost:8000/api/chat"


async def test_hybrid_search(query_obj: dict):
    """Test hybrid search with filters"""
    async with httpx.AsyncClient() as client:
        # Hybrid search request
        request_body = {
            "query": query_obj["query"],           # Vector search
            "text_query": query_obj["text_query"], # Text/keyword search
            "doc_type": "code_chunk",              # Filter to code only
            "limit": 10,
            "min_file_length": 100,                # Filter out very small files
            "max_file_length": 50000               # Increased to include larger files (some expected files are ~11k chars)
        }

        response = await client.post(
            f"{API_BASE}/search",
            json=request_body,
            timeout=30.0
        )

        if response.status_code != 200:
            return {"error": f"Search failed: {response.status_code}"}

        return response.json()


async def main():
    print("=" * 80)
    print("HYBRID SEARCH QUALITY TEST (No LLM)")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Mode: Hybrid (Vector + Text + Filters)")
    print(f"Filter: doc_type=code_chunk")
    print("=" * 80)

    total_matches = 0
    total_expected = 0

    for i, test_query in enumerate(TEST_QUERIES, 1):
        print(f"\n{'#' * 80}")
        print(f"QUERY {i}/{len(TEST_QUERIES)}: {test_query['category']}")
        print(f"{'#' * 80}")
        print(f"Query: {test_query['query']}")
        print(f"Keywords: {test_query['text_query']}")
        print(f"Expected files: {', '.join(test_query['expected_files'])}")
        print(f"Expected repos: {', '.join(test_query['expected_repos'])}")
        print()

        # Run search
        result = await test_hybrid_search(test_query)

        if "error" in result:
            print(f"❌ ERROR: {result['error']}")
            continue

        print(f"Search Mode: {result.get('search_mode', 'unknown')}")
        print(f"Documents Retrieved: {result.get('count', 0)}")
        print()

        # Analyze top 10 results
        expected_files = test_query['expected_files']
        expected_repos = test_query['expected_repos']

        matches = []
        for j, doc in enumerate(result.get('results', [])[:10], 1):
            repo_id = doc.get('repo_id', 'unknown')
            file_path = doc.get('file_path', 'unknown')
            score = doc.get('score', 0)

            # Check if file matches expectations
            file_match = any(exp in file_path for exp in expected_files)
            repo_match = any(exp in repo_id for exp in expected_repos)

            marker = "✓" if (file_match and repo_match) else " "

            if file_match and repo_match:
                matches.append(file_path)

            print(f"{marker} {j}. {repo_id}/{file_path}")
            print(f"   Score: {score:.2f}")

            # Show preview for top 3
            if j <= 3:
                preview = doc.get('content', '')[:150]
                print(f"   Preview: {preview}...")
            print()

        # Summary for this query
        total_expected += len(expected_files)
        total_matches += len(matches)

        match_rate = len(matches) / len(expected_files) * 100 if expected_files else 0
        print(f"{'=' * 80}")
        print(f"MATCH RATE: {len(matches)}/{len(expected_files)} expected files found ({match_rate:.0f}%)")
        if matches:
            print(f"Matched files: {', '.join(matches)}")
        print(f"{'=' * 80}")

    # Overall summary
    print(f"\n{'=' * 80}")
    print("OVERALL SUMMARY")
    print(f"{'=' * 80}")
    overall_precision = total_matches / total_expected * 100 if total_expected else 0
    print(f"Total matches: {total_matches}/{total_expected} ({overall_precision:.1f}%)")
    print()

    if overall_precision < 50:
        print("⚠️  POOR SEARCH QUALITY - Need to investigate:")
        print("   1. Are embeddings capturing the right semantics?")
        print("   2. Is text search working properly?")
        print("   3. Do we need better keyword extraction?")
        print("   4. Should we filter out empty __init__.py files?")
    elif overall_precision < 80:
        print("⚠️  MODERATE SEARCH QUALITY - Room for improvement")
    else:
        print("✓ GOOD SEARCH QUALITY")

    print(f"{'=' * 80}")


if __name__ == "__main__":
    asyncio.run(main())
