#!/usr/bin/env python3
"""Test API connectivity for Akriti LoRA pipeline."""

import os
from pathlib import Path
from dotenv import load_dotenv
import httpx

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

API_BASE = os.getenv("CODESMRITI_API_URL", "http://macstudio.local")
API_USERNAME = os.getenv("CODESMRITI_USERNAME", "")
API_PASSWORD = os.getenv("CODESMRITI_PASSWORD", "")


def test_rag_api():
    """Test RAG API connectivity."""
    print("Testing RAG API...")

    # Get token
    response = httpx.post(
        f"{API_BASE}/api/auth/login",
        json={"email": API_USERNAME, "password": API_PASSWORD},
        timeout=30.0,
        verify=False
    )
    response.raise_for_status()
    token = response.json()["token"]
    print(f"  Auth: OK")

    headers = {"Authorization": f"Bearer {token}"}

    # Test repos endpoint
    response = httpx.get(
        f"{API_BASE}/api/rag/repos",
        headers=headers,
        timeout=30.0,
        verify=False
    )
    repos = response.json().get("repos", [])
    print(f"  Repos: {len(repos)} found")

    # Test search endpoint
    response = httpx.post(
        f"{API_BASE}/api/rag/search",
        json={"query": "workflow", "level": "module", "limit": 3},
        headers=headers,
        timeout=60.0,
        verify=False
    )
    results = response.json().get("results", [])
    print(f"  Search: {len(results)} results for 'workflow'")

    return True


def test_lm_studio():
    """Test LM Studio connectivity."""
    print("\nTesting LM Studio...")

    try:
        response = httpx.get("http://localhost:1234/v1/models", timeout=10)
        models = response.json().get("data", [])
        print(f"  Models loaded: {len(models)}")
        for m in models:
            print(f"    - {m.get('id', 'unknown')}")
        return True
    except httpx.ConnectError:
        print("  Not running (start LM Studio first)")
        return False


def main():
    print("Akriti LoRA - Connectivity Test")
    print("=" * 40)

    rag_ok = False
    lm_ok = False

    try:
        rag_ok = test_rag_api()
    except Exception as e:
        print(f"  Error: {e}")

    try:
        lm_ok = test_lm_studio()
    except Exception as e:
        print(f"  Error: {e}")

    print("\n" + "=" * 40)
    print(f"RAG API: {'OK' if rag_ok else 'FAIL'}")
    print(f"LM Studio: {'OK' if lm_ok else 'FAIL'}")

    if rag_ok and lm_ok:
        print("\nReady to run generate_qa_pairs.py!")
    elif rag_ok:
        print("\nStart LM Studio and load qwen/qwen3-next-80b to continue.")


if __name__ == "__main__":
    main()
