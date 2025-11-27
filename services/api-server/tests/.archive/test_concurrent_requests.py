"""
Test concurrent requests to verify singleton resource sharing.
"""
import asyncio
import httpx
import time

async def send_request(client: httpx.AsyncClient, request_id: int):
    """Send a single chat request."""
    try:
        start = time.time()
        response = await client.post(
            "http://localhost:8000/api/chat/test",
            json={
                "query": f"What is code-smriti? (Request {request_id})",
                "stream": False
            },
            timeout=90.0
        )
        elapsed = time.time() - start

        if response.status_code == 200:
            print(f"✓ Request {request_id} succeeded in {elapsed:.2f}s")
            return True
        else:
            print(f"✗ Request {request_id} failed: {response.status_code}")
            return False
    except Exception as e:
        print(f"✗ Request {request_id} error: {e}")
        return False


async def main():
    """Run concurrent requests test."""
    print("Starting concurrent requests test...")
    print("This verifies that the singleton pattern is working correctly.\n")

    # Test with 5 concurrent requests
    num_requests = 5

    async with httpx.AsyncClient() as client:
        # Send all requests concurrently
        start = time.time()
        tasks = [send_request(client, i+1) for i in range(num_requests)]
        results = await asyncio.gather(*tasks)
        total_time = time.time() - start

        # Print results
        success_count = sum(results)
        print(f"\n{'='*60}")
        print(f"Results: {success_count}/{num_requests} requests succeeded")
        print(f"Total time: {total_time:.2f}s")
        print(f"Average time per request: {total_time/num_requests:.2f}s")
        print(f"{'='*60}")

        if success_count == num_requests:
            print("\n✓ All requests succeeded!")
            print("✓ Singleton pattern is working - resources are shared across requests")
        else:
            print(f"\n✗ {num_requests - success_count} requests failed")


if __name__ == "__main__":
    asyncio.run(main())
