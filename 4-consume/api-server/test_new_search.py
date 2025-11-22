#!/usr/bin/env python3
"""
Test the updated FTS-based search function
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient
from app.chat.manual_rag_agent import RAGContext, search_code_tool
from sentence_transformers import SentenceTransformer

async def main():
    print("=" * 70)
    print("TESTING UPDATED search_code_tool WITH FTS REST API")
    print("=" * 70)

    # Initialize
    db = CouchbaseClient()
    embedding_model = SentenceTransformer(
        "nomic-ai/nomic-embed-text-v1.5",
        trust_remote_code=True
    )

    async with httpx.AsyncClient() as http_client:
        ctx = RAGContext(
            db=db,
            tenant_id="code_kosha",
            ollama_host="http://localhost:11434",
            http_client=http_client,
            embedding_model=embedding_model
        )

        # Test 1: Search without filter
        print("\n" + "-" * 70)
        print("TEST 1: Search for 'background consumer' without repo filter")
        print("-" * 70)

        results = await search_code_tool(ctx, "background consumer", limit=5)

        print(f"\nResults: {len(results)}")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['repo_id']}/{r['file_path']}")
            print(f"   Score: {r['score']:.2f} | Lines {r['start_line']}-{r['end_line']}")

        # Test 2: Search with repo filter
        print("\n" + "-" * 70)
        print("TEST 2: Search for 'background consumer' WITH repo filter")
        print("-" * 70)

        results_filtered = await search_code_tool(
            ctx,
            "background consumer",
            limit=5,
            repo_filter="kbhalerao/labcore"
        )

        print(f"\nResults: {len(results_filtered)}")
        for i, r in enumerate(results_filtered, 1):
            print(f"{i}. {r['repo_id']}/{r['file_path']}")
            print(f"   Score: {r['score']:.2f} | Lines {r['start_line']}-{r['end_line']}")

        # Test 3: Search for documents (not code)
        print("\n" + "-" * 70)
        print("TEST 3: Search for 'README' in documents (not code)")
        print("-" * 70)

        results_docs = await search_code_tool(
            ctx,
            "introduction overview README",
            limit=5,
            doc_type="document"
        )

        print(f"\nResults: {len(results_docs)}")
        for i, r in enumerate(results_docs, 1):
            print(f"{i}. {r['repo_id']}/{r['file_path']}")
            print(f"   Score: {r['score']:.2f} | Type: {r['type']}")

        # Test 4: Verify scores are non-zero
        print("\n" + "=" * 70)
        print("VALIDATION:")
        print("=" * 70)

        all_scores = [r['score'] for r in results + results_filtered]
        non_zero = [s for s in all_scores if s > 0]

        print(f"Total scores: {len(all_scores)}")
        print(f"Non-zero scores: {len(non_zero)}")
        print(f"Score range: {min(all_scores):.2f} - {max(all_scores):.2f}")

        if len(non_zero) == len(all_scores):
            print("\n✓ SUCCESS! All scores are non-zero (FTS working!)")
        else:
            print(f"\n❌ FAILURE! {len(all_scores) - len(non_zero)} zero scores found")

        print("=" * 70)

asyncio.run(main())
