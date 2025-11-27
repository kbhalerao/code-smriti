#!/usr/bin/env python3
"""Quick test of the search endpoint"""
import asyncio
import httpx

async def test_search():
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            print("Sending search request...")
            response = await client.post(
                'http://localhost:8000/api/chat/search',
                json={'query': 'test query'}
            )
            print(f"Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"Sources found: {len(data.get('sources', []))}")
                print(f"Response: {data}")
            else:
                print(f"Error: {response.text}")
    except Exception as e:
        print(f"Exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_search())
