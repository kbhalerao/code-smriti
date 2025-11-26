#!/usr/bin/env python3
"""Find where expected Svelte files rank in search results"""
import asyncio
import httpx


async def find_rank(query_text: str, expected_file: str):
    """Find the rank of expected file in search results"""

    print(f"\nQUERY: {query_text}")
    print(f"LOOKING FOR: {expected_file}")
    print("-" * 80)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get more results to see where it ranks
        response = await client.post(
            'http://localhost:8000/api/chat/search',
            json={
                "query": query_text,
                "limit": 20,  # Max allowed
                "doc_type": "code_chunk"
            }
        )

        if response.status_code != 200:
            print(f"❌ Search failed: {response.status_code}")
            return

        data = response.json()
        results = data.get('results', [])

        # Find the expected file
        found_rank = None
        for i, r in enumerate(results, 1):
            if expected_file in r['file_path'] and 'ask-kev-2026' in r['repo_id']:
                found_rank = i
                print(f"✅ Found at rank #{i} with score {r['score']:.3f}")
                print(f"   {r['repo_id']}/{r['file_path']}")
                break

        if not found_rank:
            print(f"❌ NOT found in top 50 results")

        # Show what ranked higher
        print(f"\nTop 3 results that ranked higher:")
        for i, r in enumerate(results[:3], 1):
            print(f"  #{i} [{r['score']:.3f}] {r['repo_id']}/{r['file_path']}")


async def main():
    await find_rank(
        "Svelte 5 component with runes for state management",
        "ChatInput.svelte"
    )

    await find_rank(
        "Svelte chat interface with suggestion buttons",
        "SuggestionButton.svelte"
    )


if __name__ == "__main__":
    asyncio.run(main())
