#!/usr/bin/env python3
"""
Compare search quality (raw documents) vs RAG quality (LLM-processed).

Tests 3 queries:
1. /api/chat/search - Returns raw search results
2. /api/chat/test - Returns LLM-narrated RAG answer

This helps evaluate:
- Search relevance (are the right documents retrieved?)
- RAG quality (does the LLM synthesize good answers from the documents?)
"""
import asyncio
import httpx
import json
from datetime import datetime

# Test queries from eval suite
TEST_QUERIES = [
    {
        "query": "Django Channels background worker with job counter decorator",
        "description": "Framework pattern - medium difficulty",
        "expected_files": ["orders/consumers.py", "common/consumer_decorators.py"]
    },
    {
        "query": "Svelte 5 component with runes for state management",
        "description": "UI component - medium difficulty",
        "expected_files": ["src/lib/components/chat/ChatInput.svelte"]
    },
    {
        "query": "Redis integration for background job tracking",
        "description": "Architecture - medium difficulty",
        "expected_files": ["common/consumer_decorators.py", "common/redis_lock.py"]
    }
]

API_BASE = "http://localhost:8000/api/chat"


async def test_search_endpoint(query: str):
    """Test raw search endpoint - returns documents only"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/search",
            json={"query": query, "limit": 5},
            timeout=30.0
        )

        if response.status_code != 200:
            return {"error": f"Search failed: {response.status_code}"}

        return response.json()


async def test_rag_endpoint(query: str):
    """Test RAG endpoint - returns LLM-narrated answer"""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_BASE}/test",
            json={"query": query, "stream": False},
            timeout=60.0
        )

        if response.status_code != 200:
            return {"error": f"RAG failed: {response.status_code}"}

        return response.json()


async def main():
    print("=" * 80)
    print("SEARCH QUALITY vs RAG QUALITY COMPARISON")
    print("=" * 80)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"API Base: {API_BASE}")
    print(f"Test Queries: {len(TEST_QUERIES)}")
    print("=" * 80)

    results = []

    for i, test_case in enumerate(TEST_QUERIES, 1):
        query = test_case["query"]
        description = test_case["description"]
        expected_files = test_case.get("expected_files", [])

        print(f"\n{'#' * 80}")
        print(f"QUERY {i}/{len(TEST_QUERIES)}: {description}")
        print(f"{'#' * 80}")
        print(f"Question: {query}")
        print(f"Expected files: {', '.join(expected_files)}")
        print()

        # Test 1: Raw Search
        print("-" * 80)
        print("PART 1: RAW SEARCH RESULTS (No LLM)")
        print("-" * 80)

        search_result = await test_search_endpoint(query)

        if "error" in search_result:
            print(f"❌ ERROR: {search_result['error']}")
        else:
            print(f"Search Mode: {search_result.get('search_mode', 'unknown')}")
            print(f"Documents Retrieved: {search_result.get('count', 0)}")
            print()

            # Show top 3 documents
            retrieved_files = []
            for j, doc in enumerate(search_result.get('results', [])[:3], 1):
                file_path = doc.get('file_path', 'unknown')
                retrieved_files.append(file_path)

                # Check if this file is in expected files
                is_expected = any(exp in file_path for exp in expected_files)
                marker = "✓" if is_expected else " "

                print(f"{marker} {j}. {doc.get('repo_id', 'unknown')}/{file_path}")
                print(f"   Score: {doc.get('score', 0):.2f}")
                print(f"   Preview: {doc.get('content', '')[:100]}...")
                print()

            # Show match summary
            matches = [f for f in retrieved_files if any(exp in f for exp in expected_files)]
            print(f"Match rate: {len(matches)}/3 expected files found in top results")
            print()

        # Test 2: RAG with LLM
        print("-" * 80)
        print("PART 2: RAG ANSWER (LLM-Processed)")
        print("-" * 80)

        rag_result = await test_rag_endpoint(query)

        if "error" in rag_result:
            print(f"❌ ERROR: {rag_result['error']}")
        else:
            answer = rag_result.get('answer', 'No answer generated')
            print(answer)
            print()
            print(f"Metadata: {rag_result.get('metadata', {})}")

        # Store results for summary
        results.append({
            "query": query,
            "search": search_result,
            "rag": rag_result
        })

        print()

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    for i, result in enumerate(results, 1):
        search_count = result['search'].get('count', 0) if 'error' not in result['search'] else 0
        rag_success = 'error' not in result['rag']

        print(f"\nQuery {i}: {result['query'][:60]}...")
        print(f"  Raw Search: {search_count} documents retrieved")
        print(f"  RAG Answer: {'✓ Generated' if rag_success else '✗ Failed'}")

    print("\n" + "=" * 80)
    print("EVALUATION NOTES:")
    print("=" * 80)
    print("""
1. Search Quality Assessment:
   - Are the retrieved documents relevant to the query?
   - Do they contain the information needed to answer the question?
   - Are the scores reasonable (higher = more relevant)?

2. RAG Quality Assessment:
   - Does the answer synthesize information from multiple documents?
   - Is the answer accurate and coherent?
   - Does it cite specific code examples when appropriate?

3. Hybrid Search Testing:
   - Try adding text_query parameter for keyword matching
   - Compare vector-only vs hybrid results
   - Test file_path_pattern filtering

Example hybrid search request:
  {
    "query": "background consumer",
    "text_query": "BackgroundConsumer class",
    "file_path_pattern": "*.py",
    "limit": 5
  }
    """)
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
