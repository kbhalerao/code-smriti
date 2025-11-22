#!/usr/bin/env python3
"""Direct test of search function bypassing HTTP"""
import asyncio
import time
from app.database.couchbase_client import CouchbaseClient
from app.chat.manual_rag_agent import RAGContext, search_code_tool
from sentence_transformers import SentenceTransformer
import httpx
from loguru import logger

async def test_direct_search():
    """Test search function directly"""

    print("=" * 70)
    print("DIRECT SEARCH TEST")
    print("=" * 70)

    # Step 1: Initialize Couchbase
    print("\n[1/4] Initializing Couchbase...")
    start = time.time()
    db = CouchbaseClient()  # Uses config from environment
    print(f"  ✓ Couchbase initialized in {time.time() - start:.2f}s")

    # Step 2: Initialize embedding model
    print("\n[2/4] Loading embedding model (nomic-embed-text-v1.5)...")
    start = time.time()
    embedding_model = SentenceTransformer(
        "nomic-ai/nomic-embed-text-v1.5",
        trust_remote_code=True
    )
    print(f"  ✓ Embedding model loaded in {time.time() - start:.2f}s")

    # Step 3: Test embedding generation
    print("\n[3/4] Generating test embedding...")
    start = time.time()
    test_text = "search_document: Django Channels background worker"
    test_embedding = embedding_model.encode(test_text).tolist()
    print(f"  ✓ Embedding generated in {time.time() - start:.2f}s")
    print(f"  Embedding dimensions: {len(test_embedding)}")

    # Step 4: Test search function
    print("\n[4/4] Running vector search...")
    start = time.time()

    async with httpx.AsyncClient() as http_client:
        ctx = RAGContext(
            db=db,
            tenant_id="code_kosha",
            ollama_host="http://localhost:11434",
            http_client=http_client,
            embedding_model=embedding_model
        )

        results = await search_code_tool(
            ctx,
            query="Django Channels background worker",
            limit=10
        )

    search_time = time.time() - start
    print(f"  ✓ Vector search completed in {search_time:.2f}s")
    print(f"  Results found: {len(results)}")

    # Display results
    if results:
        print("\n" + "=" * 70)
        print("TOP 5 RESULTS:")
        print("=" * 70)
        for i, r in enumerate(results[:5], 1):
            print(f"\n{i}. {r.get('repo_id', 'unknown')}/{r.get('file_path', 'unknown')}")
            print(f"   Score: {r.get('score', 0.0):.4f}")
            print(f"   Language: {r.get('language', 'unknown')}")
            content = r.get('content', '')[:100]
            print(f"   Preview: {content}...")

    print("\n" + "=" * 70)
    print("PERFORMANCE SUMMARY:")
    print("=" * 70)
    print(f"Total search time: {search_time:.2f}s")
    print(f"Results per second: {len(results) / search_time if search_time > 0 else 0:.2f}")
    print("=" * 70)

if __name__ == '__main__':
    asyncio.run(test_direct_search())
