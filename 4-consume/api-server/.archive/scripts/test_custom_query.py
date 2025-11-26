#!/usr/bin/env python3
"""Test RAG with custom query"""
import asyncio
import httpx
import json
import sys


async def test_custom_query(query: str):
    """Test a custom query through the RAG pipeline"""

    print("=" * 80)
    print("RAG PIPELINE TEST - CUSTOM QUERY")
    print("=" * 80)
    print(f"\nQuery: {query}\n")

    async with httpx.AsyncClient(timeout=120.0) as client:
        print("Sending request to /api/chat/test...")
        response = await client.post(
            'http://localhost:8000/api/chat/test',
            json={
                "query": query,
                "stream": False
            }
        )

        if response.status_code == 200:
            data = response.json()
            answer = data.get('answer', '')
            metadata = data.get('metadata', {})

            print("\n" + "=" * 80)
            print("ANSWER:")
            print("=" * 80)
            print(answer)
            print("\n" + "=" * 80)

            print(f"\nMetadata:")
            print(f"  Tenant: {metadata.get('tenant_id', 'N/A')}")
            print(f"  Conversation length: {metadata.get('conversation_length', 'N/A')}")

            # Check for citations
            has_citations = '[' in answer and ']' in answer and '/' in answer
            has_sources = 'Sources:' in answer or 'sources:' in answer.lower()

            print(f"\nCitation check:")
            print(f"  Has inline citations: {'✅' if has_citations else '❌'}")
            print(f"  Has Sources section: {'✅' if has_sources else '❌'}")

        else:
            print(f"❌ Request failed: {response.status_code}")
            print(f"Response: {response.text}")


if __name__ == "__main__":
    query = sys.argv[1] if len(sys.argv) > 1 else "Show me a canonical example of using access control queryset in models"
    asyncio.run(test_custom_query(query))
