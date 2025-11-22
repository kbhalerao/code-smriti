#!/usr/bin/env python3
"""
Test hybrid text+vector search and file_path filtering
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient
from app.chat.manual_rag_agent import RAGContext, search_code_tool
from sentence_transformers import SentenceTransformer

async def main():
    print("=" * 70)
    print("TESTING HYBRID SEARCH & FILE PATH FILTERING")
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

        # Test 1: Vector-only search (baseline)
        print("\n" + "-" * 70)
        print("TEST 1: Vector-only search (semantic similarity)")
        print("-" * 70)

        results = await search_code_tool(
            ctx,
            query="background consumer processing",
            limit=3
        )

        print(f"Results: {len(results)}")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['repo_id']}/{r['file_path']}")
            print(f"   Score: {r['score']:.2f}")

        # Test 2: Text-only search (BM25 keyword matching)
        print("\n" + "-" * 70)
        print("TEST 2: Text-only search (keyword: 'BackgroundConsumer')")
        print("-" * 70)

        results = await search_code_tool(
            ctx,
            query=None,  # No vector search
            text_query="BackgroundConsumer",
            limit=3
        )

        print(f"Results: {len(results)}")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['repo_id']}/{r['file_path']}")
            print(f"   Score: {r['score']:.2f}")

        # Test 3: Hybrid search (text + vector)
        print("\n" + "-" * 70)
        print("TEST 3: Hybrid search (semantic + keyword)")
        print("-" * 70)

        results = await search_code_tool(
            ctx,
            query="background consumer processing",  # Semantic
            text_query="BackgroundConsumer class",    # Keywords
            limit=3
        )

        print(f"Results: {len(results)}")
        print("(Scores combine semantic similarity + keyword relevance)")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['repo_id']}/{r['file_path']}")
            print(f"   Score: {r['score']:.2f}")

        # Test 4: File path filtering - Python files only
        print("\n" + "-" * 70)
        print("TEST 4: Filter by file type (*.py)")
        print("-" * 70)

        results = await search_code_tool(
            ctx,
            query="background consumer",
            file_path_pattern="*.py",
            limit=5
        )

        print(f"Results: {len(results)}")
        for i, r in enumerate(results, 1):
            file_ext = r['file_path'].split('.')[-1] if '.' in r['file_path'] else 'none'
            print(f"{i}. {r['file_path']} (ext: {file_ext})")
            print(f"   Score: {r['score']:.2f}")

        # Test 5: File path filtering - Files containing 'consumer'
        print("\n" + "-" * 70)
        print("TEST 5: Filter by filename pattern (*consumer*)")
        print("-" * 70)

        results = await search_code_tool(
            ctx,
            query="background processing",
            file_path_pattern="*consumer*",
            limit=5
        )

        print(f"Results: {len(results)}")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['file_path']}")
            print(f"   Has 'consumer': {'consumer' in r['file_path'].lower()}")
            print(f"   Score: {r['score']:.2f}")

        # Test 6: Combined filters - README files in a specific repo
        print("\n" + "-" * 70)
        print("TEST 6: Hybrid + repo + file filter (README in kbhalerao/labcore)")
        print("-" * 70)

        results = await search_code_tool(
            ctx,
            query="getting started installation",
            text_query="README documentation",
            repo_filter="kbhalerao/labcore",
            file_path_pattern="*README*",
            doc_type="document",
            limit=3
        )

        print(f"Results: {len(results)}")
        for i, r in enumerate(results, 1):
            print(f"{i}. {r['repo_id']}/{r['file_path']}")
            print(f"   Type: {r['type']}, Score: {r['score']:.2f}")

        print("\n" + "=" * 70)
        print("✓ ALL TESTS COMPLETE!")
        print("=" * 70)
        print("\nSearch Modes Available:")
        print("  • Vector-only: Semantic similarity search")
        print("  • Text-only: Keyword/BM25 search on content")
        print("  • Hybrid: Combined text + vector ranking")
        print("\nFiltering Options:")
        print("  • repo_filter: Filter by repository")
        print("  • file_path_pattern: Wildcard file path matching")
        print("  • doc_type: code_chunk, document, or commit")
        print("=" * 70)

asyncio.run(main())
