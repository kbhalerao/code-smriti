#!/usr/bin/env python3
"""
Compare embeddings between original chunk and top FTS result
to understand why exact embedding match doesn't rank #1
"""
import numpy as np
from app.database.couchbase_client import CouchbaseClient

def dot_product_similarity(vec1, vec2):
    """Calculate dot product similarity"""
    return np.dot(vec1, vec2)

def main():
    print("=" * 70)
    print("EMBEDDING COMPARISON ANALYSIS")
    print("=" * 70)

    db = CouchbaseClient()

    # Original chunk that we're searching for
    original_id = "0359c38bfd532519a85c9294d9f71307febb1ec14336e0ab7d27dc5e4f767f6d"

    # Top FTS result
    top_result_id = "bb3f13511e65be6c82a56f494b6d6b1c790b7513340a9502af614f0d434ff515"

    print(f"\nFetching documents...")
    print(f"Original chunk: {original_id}")
    print(f"Top FTS result: {top_result_id}")

    # Fetch both documents
    query = """
        SELECT META().id, repo_id, file_path, content, `language`, embedding
        FROM `code_kosha`
        WHERE META().id IN [$id1, $id2]
    """

    from couchbase.options import QueryOptions
    result = db.cluster.query(
        query,
        QueryOptions(named_parameters={"id1": original_id, "id2": top_result_id})
    )

    docs = {doc['id']: doc for doc in result}

    if original_id not in docs:
        print(f"\n❌ Original chunk not found in database!")
        return

    if top_result_id not in docs:
        print(f"\n❌ Top FTS result not found in database!")
        return

    original_doc = docs[original_id]
    top_doc = docs[top_result_id]

    print(f"\n✓ Both documents fetched")

    # Display document info
    print(f"\n" + "-" * 70)
    print(f"ORIGINAL CHUNK:")
    print(f"-" * 70)
    print(f"File: {original_doc['file_path']}")
    print(f"Repo: {original_doc['repo_id']}")
    print(f"Language: {original_doc['language']}")
    print(f"Content preview: {original_doc['content'][:150]}...")

    print(f"\n" + "-" * 70)
    print(f"TOP FTS RESULT:")
    print(f"-" * 70)
    print(f"File: {top_doc['file_path']}")
    print(f"Repo: {top_doc['repo_id']}")
    print(f"Language: {top_doc['language']}")
    print(f"Content preview: {top_doc['content'][:150]}...")

    # Get embeddings
    original_emb = original_doc.get('embedding')
    top_emb = top_doc.get('embedding')

    if not original_emb:
        print(f"\n❌ Original chunk has no embedding!")
        return

    if not top_emb:
        print(f"\n❌ Top result has no embedding!")
        return

    print(f"\n" + "-" * 70)
    print(f"EMBEDDING ANALYSIS:")
    print(f"-" * 70)
    print(f"Original embedding dims: {len(original_emb)}")
    print(f"Top result embedding dims: {len(top_emb)}")

    # Calculate similarities
    original_to_original = dot_product_similarity(original_emb, original_emb)
    original_to_top = dot_product_similarity(original_emb, top_emb)

    print(f"\nSimilarity scores (dot product):")
    print(f"  Original to itself:  {original_to_original:.6f}")
    print(f"  Original to top result: {original_to_top:.6f}")

    # This should be close to 378.3 (the FTS score for top result)
    print(f"\n  FTS score for top result: 378.327515")
    print(f"  Our calculated similarity: {original_to_top:.6f}")

    if abs(original_to_top - 378.327515) < 1.0:
        print(f"\n✓ Our calculation matches FTS score!")
    else:
        print(f"\n⚠️  Our calculation differs from FTS score")

    # Check if original should rank higher
    print(f"\n" + "=" * 70)
    print(f"CONCLUSION:")
    print(f"=" * 70)

    if original_to_original > original_to_top:
        print(f"✓ Original to itself ({original_to_original:.2f}) > Original to top ({original_to_top:.2f})")
        print(f"  → Original SHOULD rank #1 but doesn't appear in top 10")
        print(f"  → This indicates an FTS indexing or retrieval issue!")
    else:
        print(f"❌ Top result ({original_to_top:.2f}) > Original to itself ({original_to_original:.2f})")
        print(f"  → Something is very wrong with embeddings or similarity calculation")

    print("=" * 70)

if __name__ == '__main__':
    main()
