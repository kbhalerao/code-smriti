#!/usr/bin/env python3
"""
Simple Vector Search Test
Tests the vector search index directly via FTS API
"""

import requests
from requests.auth import HTTPBasicAuth
import json

def test_vector_search():
    """Test vector search with a dummy query"""
    print("="*70)
    print("Simple Vector Search Test")
    print("="*70)

    # Create a dummy 768-dimensional vector
    query_vector = [0.1] * 768

    # Build FTS query
    search_request = {
        "query": {
            "match_none": {}
        },
        "knn": [
            {
                "field": "embedding",
                "vector": query_vector,
                "k": 5
            }
        ],
        "size": 5,
        "fields": ["*"]
    }

    # Perform search
    url = "http://localhost:8094/api/index/code_vector_index/query"
    import os
    auth = HTTPBasicAuth(
        os.getenv("COUCHBASE_USERNAME", "Administrator"),
        os.environ["COUCHBASE_PASSWORD"]
    )

    print("\n1. Sending vector search query...")
    print(f"   Vector dimensions: {len(query_vector)}")
    print(f"   Requesting top-5 results")

    try:
        response = requests.post(
            url,
            json=search_request,
            auth=auth,
            headers={"Content-Type": "application/json"},
            timeout=10.0
        )
        response.raise_for_status()

        result = response.json()

        # Parse results
        hits = result.get("hits", [])
        total_hits = result.get("total_hits", 0)

        print(f"\n2. Results:")
        print(f"   Total matches: {total_hits}")
        print(f"   Returned: {len(hits)}")

        if hits:
            print(f"\n3. Top 3 results:")
            for i, hit in enumerate(hits[:3], 1):
                doc_id = hit.get("id")
                score = hit.get("score", 0.0)
                print(f"   {i}. {doc_id[:60]}... (score: {score:.4f})")

        print("\n" + "="*70)
        print("✓ Vector search is working!")
        print("="*70)
        return True

    except requests.exceptions.RequestException as e:
        print(f"\n✗ Error: {e}")
        return False

if __name__ == "__main__":
    success = test_vector_search()
    exit(0 if success else 1)
