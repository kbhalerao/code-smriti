#!/usr/bin/env python3
"""Compare expected vs actual search results for Svelte queries"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient


async def test_query(query_text: str, expected_file: str):
    """Test a query and show expected vs actual"""

    print("\n" + "=" * 80)
    print(f"QUERY: {query_text}")
    print("=" * 80)

    # Get what we SHOULD find
    db = CouchbaseClient()
    n1ql = f"""
        SELECT content, file_path, repo_id
        FROM `code_kosha`
        WHERE file_path LIKE '%{expected_file}%'
          AND repo_id LIKE '%ask-kev-2026%'
          AND type = 'code_chunk'
        LIMIT 1
    """

    result = db.cluster.query(n1ql)
    expected = None
    for row in result:
        expected = row
        break

    if expected:
        print(f"\n📌 EXPECTED TO FIND:")
        print(f"   {expected['repo_id']}/{expected['file_path']}")
        print(f"\n   Content preview:")
        print("   " + expected['content'][:300].replace('\n', '\n   '))
        if len(expected['content']) > 300:
            print("   ...")
    else:
        print(f"\n❌ Expected file not found: {expected_file}")
        return

    # Get what we ACTUALLY got
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            'http://localhost:8000/api/chat/search',
            json={
                "query": query_text,
                "limit": 10,
                "doc_type": "code_chunk"
            }
        )

        if response.status_code != 200:
            print(f"\n❌ Search failed: {response.status_code}")
            return

        data = response.json()
        results = data.get('results', [])

        if not results:
            print(f"\n❌ No results returned")
            return

        # Show top result
        top = results[0]
        print(f"\n🔍 TOP RESULT WE GOT:")
        print(f"   Score: {top['score']:.3f}")
        print(f"   {top['repo_id']}/{top['file_path']}")
        print(f"\n   Content preview:")
        print("   " + top['content'][:300].replace('\n', '\n   '))
        if len(top['content']) > 300:
            print("   ...")

        # Check if expected file appears in results
        for i, r in enumerate(results, 1):
            if expected_file in r['file_path'] and 'ask-kev-2026' in r['repo_id']:
                print(f"\n✅ Expected file found at position #{i}")
                print(f"   Score: {r['score']:.3f}")
                return

        print(f"\n❌ Expected file NOT in top 10 results")


async def main():
    # Test Q7: Svelte 5 component with runes
    await test_query(
        "Svelte 5 component with runes for state management",
        "ChatInput.svelte"
    )

    # Test Q29: Chat interface with suggestion buttons
    await test_query(
        "Svelte chat interface with suggestion buttons",
        "SuggestionButton.svelte"
    )


if __name__ == "__main__":
    asyncio.run(main())
