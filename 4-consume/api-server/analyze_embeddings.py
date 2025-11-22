#!/usr/bin/env python3
"""
Analyze embedding quality and distribution in the vector database.

This script investigates:
1. Embedding similarity distribution (are they well-separated?)
2. __init__.py clustering (do they dominate the space?)
3. Query-document distances (why are wrong files ranking higher?)
4. Embedding dimensionality usage
"""
import asyncio
import numpy as np
from typing import List, Dict
import httpx
from sentence_transformers import SentenceTransformer
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions
from sklearn.decomposition import PCA
from collections import defaultdict
import json

async def main():
    print("=" * 80)
    print("EMBEDDING QUALITY ANALYSIS")
    print("=" * 80)

    # Load embedding model
    print("\n1. Loading embedding model...")
    model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
    print(f"   ✓ Model loaded: {model.get_sentence_embedding_dimension()} dimensions")

    db = CouchbaseClient()

    # ==================================================================
    # PART 1: Sample embeddings from database
    # ==================================================================
    print("\n2. Sampling embeddings from database...")

    # Get sample of different types
    samples = {
        "init_files": [],
        "code_chunks": [],
        "expected_files": []
    }

    # Sample __init__.py files
    n1ql = """
        SELECT META().id, file_path, content, type, LENGTH(content) as len
        FROM `code_kosha`
        WHERE type = 'code_chunk' AND file_path LIKE '%__init__.py'
        LIMIT 20
    """
    result = db.cluster.query(n1ql)
    for row in result:
        samples["init_files"].append({
            "id": row["id"],
            "file_path": row["file_path"],
            "content": row.get("content", ""),
            "len": row.get("len", 0)
        })

    # Sample regular code chunks
    n1ql = """
        SELECT META().id, file_path, content, type, LENGTH(content) as len
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND file_path NOT LIKE '%__init__.py'
          AND repo_id = 'kbhalerao/labcore'
        LIMIT 30
    """
    result = db.cluster.query(n1ql)
    for row in result:
        samples["code_chunks"].append({
            "id": row["id"],
            "file_path": row["file_path"],
            "content": row.get("content", ""),
            "len": row.get("len", 0)
        })

    # Get expected files for Query 1
    n1ql = """
        SELECT META().id, file_path, content, repo_id, LENGTH(content) as len
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND repo_id = 'kbhalerao/labcore'
          AND (file_path LIKE '%orders/consumers.py' OR file_path LIKE '%common/consumer_decorators.py')
        LIMIT 10
    """
    result = db.cluster.query(n1ql)
    for row in result:
        samples["expected_files"].append({
            "id": row["id"],
            "file_path": row["file_path"],
            "content": row.get("content", ""),
            "repo_id": row.get("repo_id"),
            "len": row.get("len", 0)
        })

    print(f"   ✓ Sampled {len(samples['init_files'])} __init__.py files")
    print(f"   ✓ Sampled {len(samples['code_chunks'])} regular code chunks")
    print(f"   ✓ Sampled {len(samples['expected_files'])} expected files")

    # ==================================================================
    # PART 2: Fetch embeddings from FTS index
    # ==================================================================
    print("\n3. Fetching embeddings from FTS index...")

    all_doc_ids = []
    for category in samples.values():
        all_doc_ids.extend([doc["id"] for doc in category])

    # Fetch via FTS search (hack: use empty query to get docs by ID)
    embeddings_by_id = {}

    async with httpx.AsyncClient() as client:
        # Use a broad query to fetch documents
        fts_request = {
            "query": {"match_all": {}},
            "size": 100,
            "fields": ["*"]
        }

        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        fts_results = response.json()
        hits = fts_results.get('hits', [])

        print(f"   ✓ FTS returned {len(hits)} documents")

        # Note: FTS doesn't return embeddings in results, only in index
        # We'll need to re-embed or use vector search to compare

    # ==================================================================
    # PART 3: Re-embed samples for analysis
    # ==================================================================
    print("\n4. Re-embedding samples for analysis...")

    embeddings_by_category = {}

    for category_name, docs in samples.items():
        embeddings = []
        for doc in docs:
            # Embed content (use same prefix as search)
            content = doc["content"][:1000]  # Truncate for speed
            emb = model.encode(f"search_document: {content}")
            embeddings.append(emb)
        embeddings_by_category[category_name] = np.array(embeddings)
        print(f"   ✓ Embedded {len(embeddings)} {category_name}")

    # ==================================================================
    # PART 4: Compute similarity matrices
    # ==================================================================
    print("\n5. Computing similarity matrices...")
    print("=" * 80)

    def cosine_similarity_matrix(embeddings):
        """Compute pairwise cosine similarity."""
        # Normalize embeddings
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        normalized = embeddings / (norms + 1e-9)
        # Compute similarity matrix
        return np.dot(normalized, normalized.T)

    for category_name, embeddings in embeddings_by_category.items():
        if len(embeddings) < 2:
            continue

        sim_matrix = cosine_similarity_matrix(embeddings)

        # Get upper triangle (exclude diagonal)
        upper_triangle = sim_matrix[np.triu_indices_from(sim_matrix, k=1)]

        print(f"\n{category_name.upper()} Similarity Statistics:")
        print(f"  Sample size: {len(embeddings)}")
        print(f"  Mean similarity: {upper_triangle.mean():.4f}")
        print(f"  Std similarity:  {upper_triangle.std():.4f}")
        print(f"  Min similarity:  {upper_triangle.min():.4f}")
        print(f"  Max similarity:  {upper_triangle.max():.4f}")
        print(f"  Median:          {np.median(upper_triangle):.4f}")

        # Show distribution
        print(f"\n  Similarity distribution:")
        bins = [0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 0.99, 1.0]
        hist, _ = np.histogram(upper_triangle, bins=bins)
        for i in range(len(bins)-1):
            pct = 100 * hist[i] / len(upper_triangle)
            bar = "█" * int(pct / 2)
            print(f"    {bins[i]:.2f}-{bins[i+1]:.2f}: {pct:5.1f}% {bar}")

    # ==================================================================
    # PART 5: Query-to-document similarity analysis
    # ==================================================================
    print("\n" + "=" * 80)
    print("6. Query-to-Document Similarity Analysis")
    print("=" * 80)

    # Test query
    query = "Django Channels background worker with job counter decorator"
    query_embedding = model.encode(f"search_document: {query}")

    print(f"\nQuery: '{query}'")
    print(f"\nExpected files should have HIGH similarity:")
    print(f"{'File':<60} {'Similarity':>10}")
    print("-" * 71)

    # Compute similarities to expected files
    expected_sims = []
    for doc in samples["expected_files"]:
        content_emb = model.encode(f"search_document: {doc['content'][:1000]}")
        similarity = np.dot(query_embedding, content_emb) / (
            np.linalg.norm(query_embedding) * np.linalg.norm(content_emb)
        )
        expected_sims.append((doc["file_path"], similarity))
        print(f"{doc['file_path']:<60} {similarity:>10.4f}")

    print(f"\n__init__.py files should have LOW similarity:")
    print(f"{'File':<60} {'Similarity':>10}")
    print("-" * 71)

    # Compute similarities to __init__.py files
    init_sims = []
    for doc in samples["init_files"][:10]:
        content_emb = model.encode(f"search_document: {doc['content'][:1000]}")
        similarity = np.dot(query_embedding, content_emb) / (
            np.linalg.norm(query_embedding) * np.linalg.norm(content_emb)
        )
        init_sims.append((doc["file_path"], similarity))
        print(f"{doc['file_path']:<60} {similarity:>10.4f}")

    # Summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)

    avg_expected = np.mean([s[1] for s in expected_sims])
    avg_init = np.mean([s[1] for s in init_sims])

    print(f"\nAverage similarity to expected files: {avg_expected:.4f}")
    print(f"Average similarity to __init__.py:    {avg_init:.4f}")
    print(f"Difference:                           {avg_expected - avg_init:.4f}")

    if avg_expected < avg_init:
        print("\n⚠️  PROBLEM: __init__.py files are MORE similar to query than expected files!")
        print("   This explains why search is returning wrong results.")
    elif avg_expected - avg_init < 0.1:
        print("\n⚠️  PROBLEM: Expected files are only slightly more similar than __init__.py")
        print("   Insufficient separation in embedding space.")
    else:
        print("\n✓ Expected files have higher similarity - embeddings are working")
        print("  Problem likely in ranking/filtering pipeline.")

    # ==================================================================
    # PART 6: Dimensionality analysis
    # ==================================================================
    print("\n" + "=" * 80)
    print("7. Dimensionality Analysis")
    print("=" * 80)

    # Combine all embeddings
    all_embeddings = np.vstack([
        embeddings_by_category["init_files"],
        embeddings_by_category["code_chunks"],
        embeddings_by_category["expected_files"]
    ])

    print(f"\nTotal embeddings: {len(all_embeddings)}")
    print(f"Embedding dimension: {all_embeddings.shape[1]}")

    # PCA to find intrinsic dimensionality
    pca = PCA()
    pca.fit(all_embeddings)

    # Find number of components needed for 95% variance
    cumsum = np.cumsum(pca.explained_variance_ratio_)
    n_components_95 = np.argmax(cumsum >= 0.95) + 1

    print(f"\nExplained variance by top components:")
    print(f"  Top 10 components: {cumsum[9]:.2%}")
    print(f"  Top 50 components: {cumsum[49]:.2%}")
    print(f"  Components for 95% variance: {n_components_95}")

    if n_components_95 < all_embeddings.shape[1] * 0.1:
        print(f"\n⚠️  Only {n_components_95} dimensions needed for 95% variance")
        print(f"   Embedding space may be underutilized or data is not diverse")

    # ==================================================================
    # PART 7: Recommendations
    # ==================================================================
    print("\n" + "=" * 80)
    print("RECOMMENDATIONS")
    print("=" * 80)

    # Check __init__.py clustering
    init_sim_matrix = cosine_similarity_matrix(embeddings_by_category["init_files"])
    init_upper = init_sim_matrix[np.triu_indices_from(init_sim_matrix, k=1)]
    init_mean = init_upper.mean()

    print("\nBased on analysis:\n")

    if init_mean > 0.95:
        print("1. ✓ __init__.py files are highly clustered (mean similarity {:.4f})".format(init_mean))
        print("   → Exclude __init__.py at INDEX time, not query time")
        print("   → Current query-time filtering is correct but inefficient")

    if avg_expected - avg_init < 0.05:
        print("\n2. ⚠️  Poor query-document separation")
        print("   → Consider code-specific embedding model (CodeBERT, CodeT5)")
        print("   → Current model (nomic-embed-text-v1.5) may not understand code semantics")

    if n_components_95 < all_embeddings.shape[1] * 0.2:
        print("\n3. ⚠️  Low intrinsic dimensionality")
        print("   → Embeddings are not using full vector space")
        print("   → May indicate poor embedding quality or lack of diversity in data")

    # Check if we can find expected files
    max_expected_sim = max([s[1] for s in expected_sims])
    max_init_sim = max([s[1] for s in init_sims])

    if max_expected_sim > max_init_sim:
        print("\n4. ✓ Best expected file has higher similarity than best __init__.py")
        print("   → Problem is likely in FTS ranking or retrieval size")
        print("   → Increase retrieval size (already done: 100)")
        print("   → Consider implementing MMR for diversity")
    else:
        print("\n4. ⚠️  Even BEST expected file scores lower than some __init__.py files")
        print("   → Fundamental embedding quality problem")
        print("   → Need better embedding model or content preprocessing")

    print("\n" + "=" * 80)

asyncio.run(main())
