#!/usr/bin/env python3
"""
Test whether we can normalize embeddings in-place vs re-ingesting.

Compares three approaches:
1. Use stored unnormalized embeddings (baseline - broken)
2. Normalize stored embeddings in-place (fast fix?)
3. Generate fresh normalized embeddings (full re-ingest)

Tests self-retrieval with each approach to see which works.
"""
import asyncio
import httpx
import numpy as np
from sentence_transformers import SentenceTransformer
from app.database.couchbase_client import CouchbaseClient


async def test_normalization_approaches():
    print("=" * 80)
    print("IN-PLACE NORMALIZATION vs RE-INGESTION TEST")
    print("=" * 80)
    print()

    db = CouchbaseClient()

    # Get sample documents
    n1ql = """
        SELECT META().id, file_path, repo_id, content, embedding
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND file_path LIKE '%orders/consumers.py%'
          AND repo_id = 'kbhalerao/labcore'
        LIMIT 5
    """

    result = db.cluster.query(n1ql)
    docs = list(result)

    if not docs:
        print("❌ Could not find test documents")
        return

    print(f"Testing with {len(docs)} documents")
    print()

    # Load embedding model
    print("Loading embedding model...")
    model = SentenceTransformer(
        "nomic-ai/nomic-embed-text-v1.5",
        trust_remote_code=True
    )
    print(f"✓ Model loaded on device: {model.device}")
    print()

    # Pick first doc as query
    query_doc = docs[0]
    print(f"Query document (should rank #1 in self-retrieval):")
    print(f"  ID: {query_doc['id']}")
    print(f"  File: {query_doc['file_path']}")
    print()

    # Build index of all docs
    doc_index = {doc['id']: doc for doc in docs}

    # === APPROACH 1: Stored (unnormalized) embeddings ===
    print("=" * 80)
    print("APPROACH 1: Stored Unnormalized Embeddings (baseline)")
    print("=" * 80)

    stored_query_emb = np.array(query_doc['embedding'])
    stored_norm = np.linalg.norm(stored_query_emb)
    print(f"Query embedding norm: {stored_norm:.4f}")
    print()

    # Compute similarities
    results_stored = []
    for doc in docs:
        doc_emb = np.array(doc['embedding'])
        # Dot product (what FTS uses with unnormalized vectors)
        score = np.dot(stored_query_emb, doc_emb)
        results_stored.append((doc['id'], score, doc['file_path']))

    results_stored.sort(key=lambda x: x[1], reverse=True)

    print("Top 5 results:")
    for i, (doc_id, score, path) in enumerate(results_stored, 1):
        marker = "🎯" if doc_id == query_doc['id'] else " "
        print(f"{marker} {i}. Score: {score:.2f} - {path}")
    print()

    rank_stored = next((i for i, (doc_id, _, _) in enumerate(results_stored, 1) if doc_id == query_doc['id']), None)
    print(f"Query doc rank: #{rank_stored}")
    print()

    # === APPROACH 2: In-place normalization ===
    print("=" * 80)
    print("APPROACH 2: In-Place Normalization (normalize stored embeddings)")
    print("=" * 80)

    # Normalize the stored query embedding
    normalized_query_emb = stored_query_emb / stored_norm
    normalized_norm = np.linalg.norm(normalized_query_emb)
    print(f"Query embedding norm after normalization: {normalized_norm:.4f}")
    print()

    # Compute similarities with normalized vectors
    results_inplace = []
    for doc in docs:
        doc_emb = np.array(doc['embedding'])
        doc_norm = np.linalg.norm(doc_emb)
        normalized_doc_emb = doc_emb / doc_norm

        # Dot product of normalized vectors = cosine similarity
        score = np.dot(normalized_query_emb, normalized_doc_emb)
        results_inplace.append((doc['id'], score, doc['file_path']))

    results_inplace.sort(key=lambda x: x[1], reverse=True)

    print("Top 5 results:")
    for i, (doc_id, score, path) in enumerate(results_inplace, 1):
        marker = "🎯" if doc_id == query_doc['id'] else " "
        print(f"{marker} {i}. Score: {score:.4f} - {path}")
    print()

    rank_inplace = next((i for i, (doc_id, _, _) in enumerate(results_inplace, 1) if doc_id == query_doc['id']), None)
    print(f"Query doc rank: #{rank_inplace}")
    print()

    # === APPROACH 3: Fresh normalized embeddings ===
    print("=" * 80)
    print("APPROACH 3: Fresh Normalized Embeddings (re-ingest)")
    print("=" * 80)

    # Generate fresh normalized embedding for query
    query_text_with_prefix = f"search_document: {query_doc['content']}"
    fresh_query_emb = model.encode(
        query_text_with_prefix,
        convert_to_tensor=False,
        show_progress_bar=False,
        normalize_embeddings=True
    )
    fresh_query_emb = np.array(fresh_query_emb)
    fresh_norm = np.linalg.norm(fresh_query_emb)
    print(f"Query embedding norm: {fresh_norm:.4f}")
    print()

    # Generate fresh normalized embeddings for all docs
    print("Generating fresh embeddings for all documents...")
    doc_texts = [f"search_document: {doc['content']}" for doc in docs]
    fresh_embeddings = model.encode(
        doc_texts,
        convert_to_tensor=False,
        show_progress_bar=False,
        normalize_embeddings=True,
        batch_size=len(docs)
    )

    # Compute similarities with fresh normalized embeddings
    results_fresh = []
    for doc, fresh_emb in zip(docs, fresh_embeddings):
        fresh_emb = np.array(fresh_emb)
        score = np.dot(fresh_query_emb, fresh_emb)
        results_fresh.append((doc['id'], score, doc['file_path']))

    results_fresh.sort(key=lambda x: x[1], reverse=True)

    print("Top 5 results:")
    for i, (doc_id, score, path) in enumerate(results_fresh, 1):
        marker = "🎯" if doc_id == query_doc['id'] else " "
        print(f"{marker} {i}. Score: {score:.4f} - {path}")
    print()

    rank_fresh = next((i for i, (doc_id, _, _) in enumerate(results_fresh, 1) if doc_id == query_doc['id']), None)
    print(f"Query doc rank: #{rank_fresh}")
    print()

    # === COMPARISON ===
    print("=" * 80)
    print("COMPARISON")
    print("=" * 80)
    print()

    print(f"Query document ranking:")
    print(f"  Approach 1 (stored unnormalized):     #{rank_stored}")
    print(f"  Approach 2 (in-place normalization):  #{rank_inplace}")
    print(f"  Approach 3 (fresh normalized):        #{rank_fresh}")
    print()

    # Compare embeddings
    print("Embedding similarity analysis:")

    # Stored vs normalized stored
    sim_stored_vs_inplace = np.dot(stored_query_emb / stored_norm, normalized_query_emb)
    print(f"  Stored vs In-place normalized: {sim_stored_vs_inplace:.6f} (should be 1.0)")

    # Stored vs fresh
    sim_stored_vs_fresh = np.dot(stored_query_emb / stored_norm, fresh_query_emb)
    print(f"  Stored vs Fresh normalized:    {sim_stored_vs_fresh:.6f}")

    # In-place vs fresh
    sim_inplace_vs_fresh = np.dot(normalized_query_emb, fresh_query_emb)
    print(f"  In-place vs Fresh normalized:  {sim_inplace_vs_fresh:.6f}")
    print()

    # Verdict
    print("=" * 80)
    print("VERDICT")
    print("=" * 80)
    print()

    if rank_inplace == 1 and rank_fresh == 1:
        print("✅ BOTH APPROACHES WORK!")
        print()
        if sim_inplace_vs_fresh > 0.999:
            print("   In-place and fresh produce IDENTICAL results")
            print("   → Can use FAST in-place normalization (no re-ingest needed)")
        else:
            print(f"   In-place and fresh differ (similarity = {sim_inplace_vs_fresh:.4f})")
            print("   → Results may vary slightly between approaches")

            # Check if top scores are identical
            top_score_inplace = results_inplace[0][1]
            top_score_fresh = results_fresh[0][1]
            print(f"   Top score (in-place): {top_score_inplace:.6f}")
            print(f"   Top score (fresh):    {top_score_fresh:.6f}")

            if abs(top_score_inplace - top_score_fresh) < 0.001:
                print("   → Scores are effectively identical, use in-place normalization")
            else:
                print("   → Recommend re-ingestion for consistency")

    elif rank_inplace == 1:
        print("✅ IN-PLACE NORMALIZATION WORKS")
        print("❌ Fresh embeddings have issues")
        print()
        print("   → Use in-place normalization")

    elif rank_fresh == 1:
        print("❌ In-place normalization FAILS")
        print("✅ Fresh embeddings work")
        print()
        print("   → MUST re-ingest with normalized embeddings")

    else:
        print("❌ BOTH APPROACHES FAIL")
        print()
        print("   → Something else is wrong (not just normalization)")

    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(test_normalization_approaches())
