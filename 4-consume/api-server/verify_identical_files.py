#!/usr/bin/env python3
"""
Pick a file with matching chunk counts and verify embeddings are identical
"""
import numpy as np
from app.database.couchbase_client import CouchbaseClient

def main():
    print("=" * 70)
    print("VERIFYING IDENTICAL FILES BETWEEN REPOS")
    print("=" * 70)

    db = CouchbaseClient()

    # Pick a file with matching chunks - articles/models.py (2 chunks each)
    test_file = "articles/models.py"

    query = """
        SELECT META().id, repo_id, file_path, content, embedding
        FROM `code_kosha`
        WHERE type='code_chunk'
          AND file_path = $file
          AND repo_id IN ['kbhalerao/labcore', 'ContinuumAgInc/topsoil2.0']
        ORDER BY repo_id, META().id
    """

    from couchbase.options import QueryOptions
    result = db.cluster.query(query, QueryOptions(named_parameters={"file": test_file}))
    chunks = list(result)

    labcore_chunks = [c for c in chunks if 'labcore' in c['repo_id']]
    topsoil_chunks = [c for c in chunks if 'topsoil' in c['repo_id']]

    print(f"\nTest file: {test_file}")
    print(f"Labcore chunks: {len(labcore_chunks)}")
    print(f"Topsoil chunks: {len(topsoil_chunks)}")

    if len(labcore_chunks) != len(topsoil_chunks):
        print(f"\n❌ Chunk counts don't match! Aborting.")
        return

    print(f"\n" + "-" * 70)
    print("COMPARING CHUNKS:")
    print("-" * 70)

    for i in range(len(labcore_chunks)):
        lc = labcore_chunks[i]
        tc = topsoil_chunks[i]

        print(f"\nChunk {i+1}:")
        print(f"  Labcore ID: {lc['id']}")
        print(f"  Topsoil ID: {tc['id']}")

        # Compare content
        content_match = lc['content'] == tc['content']
        print(f"  Content match: {content_match}")
        if not content_match:
            print(f"  Labcore content len: {len(lc['content'])}")
            print(f"  Topsoil content len: {len(tc['content'])}")

        # Compare embeddings
        lc_emb = lc.get('embedding', [])
        tc_emb = tc.get('embedding', [])

        if lc_emb and tc_emb:
            # Calculate similarity
            similarity = np.dot(lc_emb, tc_emb)
            print(f"  Embedding similarity: {similarity:.6f}")

            # Check if identical
            emb_match = np.allclose(lc_emb, tc_emb, rtol=1e-9)
            print(f"  Embeddings identical: {emb_match}")

            if not emb_match:
                diff = np.abs(np.array(lc_emb) - np.array(tc_emb))
                print(f"  Max difference: {np.max(diff):.10f}")
        else:
            print(f"  ❌ Missing embeddings!")

    # Summary
    print(f"\n" + "=" * 70)
    print("CONCLUSION:")
    print("=" * 70)

    if len(labcore_chunks) == len(topsoil_chunks):
        all_content_match = all(
            labcore_chunks[i]['content'] == topsoil_chunks[i]['content']
            for i in range(len(labcore_chunks))
        )
        all_emb_match = all(
            np.allclose(
                labcore_chunks[i].get('embedding', []),
                topsoil_chunks[i].get('embedding', []),
                rtol=1e-9
            )
            for i in range(len(labcore_chunks))
            if labcore_chunks[i].get('embedding') and topsoil_chunks[i].get('embedding')
        )

        print(f"All content matches: {all_content_match}")
        print(f"All embeddings identical: {all_emb_match}")

        if all_content_match and all_emb_match:
            print(f"\n✓ Files are IDENTICAL with same embeddings")
            print(f"  This proves the ingestion process is consistent")
        elif all_content_match and not all_emb_match:
            print(f"\n⚠️  Content matches but embeddings differ")
            print(f"  This suggests different embedding models or ingestion runs")
        else:
            print(f"\n❌ Files have been modified between repos")

    print("=" * 70)

if __name__ == '__main__':
    main()
