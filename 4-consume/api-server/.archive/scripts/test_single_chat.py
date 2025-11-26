#!/usr/bin/env python3
"""Test a single query through the full RAG chat pipeline"""
import asyncio
import httpx
import json


async def test_chat_query():
    """Test Q2 which had excellent search results"""

    query = "requeue task decorator with retry logic for async functions"

    print("=" * 80)
    print("FULL RAG PIPELINE TEST")
    print("=" * 80)
    print(f"\nQuery: {query}")
    print(f"\nExpected to find:")
    print(f"  Repo: kbhalerao/labcore")
    print(f"  File: common/consumer_decorators.py")
    print(f"  Search score: 0.727 (from previous eval)")
    print()

    # Step 1: Show what search returns
    print("=" * 80)
    print("STEP 1: SEARCH RESULTS")
    print("=" * 80)

    async with httpx.AsyncClient(timeout=60.0) as client:
        search_response = await client.post(
            'http://localhost:8000/api/chat/search',
            json={
                "query": query,
                "limit": 5,
                "doc_type": "code_chunk"
            }
        )

        if search_response.status_code == 200:
            search_data = search_response.json()
            results = search_data.get('results', [])

            print(f"\nFound {len(results)} results:")
            for i, r in enumerate(results, 1):
                marker = "🎯" if "consumer_decorators.py" in r['file_path'] else "  "
                print(f"{marker} {i}. [{r['score']:.3f}] {r['repo_id']}/{r['file_path']}")
            print()
        else:
            print(f"❌ Search failed: {search_response.status_code}")
            return

    # Step 2: Run through full RAG chat
    print("=" * 80)
    print("STEP 2: FULL RAG CHAT RESPONSE")
    print("=" * 80)

    async with httpx.AsyncClient(timeout=120.0) as client:
        chat_response = await client.post(
            'http://localhost:8000/api/chat/test',
            json={
                "query": query,
                "stream": False
            }
        )

        if chat_response.status_code == 200:
            chat_data = chat_response.json()
            answer = chat_data.get('answer', '')
            metadata = chat_data.get('metadata', {})

            print(f"\nMetadata:")
            print(f"  Conversation length: {metadata.get('conversation_length', 'N/A')}")
            print(f"\n{'='*80}")
            print("ANSWER:")
            print("=" * 80)
            print(answer)
            print("=" * 80)

            # Save full response
            output = {
                "query": query,
                "search_results": results if 'results' in locals() else [],
                "chat_answer": answer,
                "metadata": metadata
            }

            with open('/tmp/single_chat_test.json', 'w') as f:
                json.dump(output, f, indent=2)

            print(f"\n✅ Full response saved to: /tmp/single_chat_test.json")

            # Quick quality check
            print(f"\n{'='*80}")
            print("QUALITY ASSESSMENT")
            print("=" * 80)

            has_code = "```" in answer or "def " in answer or "class " in answer
            has_decorator = "decorator" in answer.lower()
            has_retry = "retry" in answer.lower() or "requeue" in answer.lower()
            has_async = "async" in answer.lower()
            answer_length = len(answer)

            print(f"  Contains code: {'✅' if has_code else '❌'}")
            print(f"  Mentions decorator: {'✅' if has_decorator else '❌'}")
            print(f"  Mentions retry/requeue: {'✅' if has_retry else '❌'}")
            print(f"  Mentions async: {'✅' if has_async else '❌'}")
            print(f"  Answer length: {answer_length} chars")

            if has_code and has_decorator and has_retry and answer_length > 200:
                print(f"\n✅ GOOD: Answer appears comprehensive and relevant")
            else:
                print(f"\n⚠️  Check response quality - may be incomplete")

        else:
            print(f"❌ Chat failed: {chat_response.status_code}")
            print(f"   Response: {chat_response.text[:500]}")


if __name__ == "__main__":
    asyncio.run(test_chat_query())
