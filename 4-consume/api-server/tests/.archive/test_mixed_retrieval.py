#!/usr/bin/env python3
"""Test mixed document + code retrieval with natural language query"""
import asyncio
import httpx
import json


async def test_mixed_retrieval():
    """Test retrieving both docs and code for access control querysets"""

    query = "Give me a canonical example of implementing access control query sets in a model"

    print("=" * 80)
    print("MIXED RETRIEVAL TEST (Docs + Code)")
    print("=" * 80)
    print(f"\nQuery: \"{query}\"")
    print("\nExpected:")
    print("  - Documentation about access control patterns")
    print("  - Code examples with QuerySet filtering")
    print("  - Model implementations with permissions/ACL")
    print()

    # Test both document types
    test_cases = [
        {"doc_type": "document", "desc": "Documentation"},
        {"doc_type": "code_chunk", "desc": "Code examples"},
    ]

    all_results = {"document": [], "code_chunk": []}

    async with httpx.AsyncClient(timeout=30.0) as client:
        for test_case in test_cases:
            doc_type = test_case["doc_type"]
            desc = test_case["desc"]

            print("=" * 80)
            print(f"Searching: {desc} (doc_type='{doc_type}')")
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
            all_results[doc_type] = results

            print(f"\nFound {len(results)} results\n")

            if results:
                for i, result in enumerate(results[:5], 1):  # Show top 5
                    repo_id = result.get('repo_id', 'unknown')
                    file_path = result.get('file_path', 'unknown')
                    score = result.get('score', 0)
                    content = result.get('content', '')

                    # Check for relevant keywords
                    keywords = {
                        'queryset': 'queryset' in content.lower(),
                        'access': 'access' in content.lower() or 'permission' in content.lower(),
                        'filter': 'filter' in content.lower(),
                        'model': 'model' in content.lower() or 'class ' in content.lower(),
                    }

                    relevant_keywords = [k for k, v in keywords.items() if v]

                    print(f"  {i}. {repo_id}/{file_path}")
                    print(f"     Score: {score:.4f}")
                    if relevant_keywords:
                        print(f"     Keywords: {', '.join(relevant_keywords)}")

                    # Show content preview
                    preview = content[:250].replace('\n', ' ')
                    if len(content) > 250:
                        preview += "..."
                    print(f"     Preview: {preview}")
                    print()

            else:
                print("❌ No results found")

            print()

    # Combined analysis
    print("=" * 80)
    print("COMBINED ANALYSIS")
    print("=" * 80)

    doc_count = len(all_results["document"])
    code_count = len(all_results["code_chunk"])

    print(f"\nResults breakdown:")
    print(f"  Documentation: {doc_count}")
    print(f"  Code examples: {code_count}")
    print(f"  Total: {doc_count + code_count}")

    if code_count > 0:
        # Analyze code results for patterns
        print(f"\nCode analysis:")

        has_queryset = sum(1 for r in all_results["code_chunk"]
                          if 'queryset' in r.get('content', '').lower())
        has_filter = sum(1 for r in all_results["code_chunk"]
                        if 'filter' in r.get('content', '').lower())
        has_permission = sum(1 for r in all_results["code_chunk"]
                            if 'permission' in r.get('content', '').lower()
                            or 'access' in r.get('content', '').lower())

        print(f"  Contains 'queryset': {has_queryset}/{code_count}")
        print(f"  Contains 'filter': {has_filter}/{code_count}")
        print(f"  Contains 'permission/access': {has_permission}/{code_count}")

        # Show best code example
        if all_results["code_chunk"]:
            best = all_results["code_chunk"][0]
            print(f"\n🎯 Best code example:")
            print(f"   {best['repo_id']}/{best['file_path']}")
            print(f"   Score: {best['score']:.4f}")

    if doc_count > 0:
        print(f"\n📄 Best documentation:")
        best_doc = all_results["document"][0]
        print(f"   {best_doc['repo_id']}/{best_doc['file_path']}")
        print(f"   Score: {best_doc['score']:.4f}")

    # Overall verdict
    print("\n" + "=" * 80)
    print("VERDICT")
    print("=" * 80)

    if doc_count > 0 and code_count > 0:
        print("✅ SUCCESS: Found both documentation and code examples")
    elif code_count > 0:
        print("⚠️  PARTIAL: Found code examples but no documentation")
    elif doc_count > 0:
        print("⚠️  PARTIAL: Found documentation but no code examples")
    else:
        print("❌ FAIL: No relevant results found")

    print()


if __name__ == "__main__":
    asyncio.run(test_mixed_retrieval())
