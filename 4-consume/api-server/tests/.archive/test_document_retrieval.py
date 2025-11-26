#!/usr/bin/env python3
"""Test document retrieval with natural language query"""
import asyncio
import httpx
import json


async def test_document_retrieval():
    """Test retrieving design guidelines about workspaces"""

    query = "retrieve the design guidelines relevant to workspaces"

    print("=" * 80)
    print("DOCUMENT RETRIEVAL TEST")
    print("=" * 80)
    print(f"\nQuery: \"{query}\"")
    print("Expected: Documentation from farmworth project about workspace design\n")

    # Try both document and code_chunk types
    test_cases = [
        {"doc_type": "document", "desc": "Document chunks (markdown/docs)"},
        {"doc_type": "code_chunk", "desc": "Code chunks (might include README)"},
    ]

    async with httpx.AsyncClient(timeout=30.0) as client:
        for test_case in test_cases:
            doc_type = test_case["doc_type"]
            desc = test_case["desc"]

            print("=" * 80)
            print(f"Testing with doc_type='{doc_type}' ({desc})")
            print("=" * 80)

            response = await client.post(
                'http://localhost:8000/api/chat/search',
                json={
                    "query": query,
                    "limit": 10,
                    "doc_type": doc_type
                }
            )

            if response.status_code != 200:
                print(f"❌ Error: {response.status_code} - {response.text}")
                continue

            data = response.json()
            results = data.get('results', [])

            print(f"\nFound {len(results)} results\n")

            if results:
                # Group by repo
                by_repo = {}
                for r in results:
                    repo = r.get('repo_id', 'unknown')
                    if repo not in by_repo:
                        by_repo[repo] = []
                    by_repo[repo].append(r)

                print(f"Results from {len(by_repo)} repositories:\n")

                for i, result in enumerate(results[:10], 1):
                    repo_id = result.get('repo_id', 'unknown')
                    file_path = result.get('file_path', 'unknown')
                    score = result.get('score', 0)
                    content = result.get('content', '')

                    # Check if it's from farmworth
                    is_farmworth = 'farmworth' in repo_id.lower()
                    marker = "🎯" if is_farmworth else "  "

                    print(f"{marker} {i}. {repo_id}/{file_path}")
                    print(f"      Score: {score:.4f}")

                    # Show content preview
                    preview = content[:200].replace('\n', ' ')
                    if len(content) > 200:
                        preview += "..."
                    print(f"      Preview: {preview}")
                    print()

                # Summary
                farmworth_count = sum(1 for r in results if 'farmworth' in r.get('repo_id', '').lower())
                if farmworth_count > 0:
                    print(f"✅ Found {farmworth_count} results from farmworth projects")
                else:
                    print(f"⚠️  No results from farmworth projects")

            else:
                print("❌ No results found")

            print()

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_document_retrieval())
