#!/usr/bin/env python3
"""
Find all chunks with the same embedding as our test chunk
to understand the duplicate situation
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient

async def main():
    print("=" * 70)
    print("FINDING DUPLICATE EMBEDDINGS")
    print("=" * 70)

    db = CouchbaseClient()

    # Get the original chunk's embedding
    original_id = "0359c38bfd532519a85c9294d9f71307febb1ec14336e0ab7d27dc5e4f767f6d"

    query = """
        SELECT META().id, repo_id, file_path, embedding
        FROM `code_kosha`
        WHERE META().id = $id
    """

    from couchbase.options import QueryOptions
    result = db.cluster.query(query, QueryOptions(named_parameters={"id": original_id}))
    chunks = list(result)

    if not chunks:
        print(f"❌ Original chunk not found!")
        return

    original_embedding = chunks[0]['embedding']

    print(f"\n✓ Got original chunk embedding (768 dims)")
    print(f"  ID: {original_id}")
    print(f"  File: {chunks[0]['file_path']}")
    print(f"  Repo: {chunks[0]['repo_id']}")

    # Search FTS for ALL results with this embedding (increase k to 50)
    print(f"\n" + "-" * 70)
    print(f"Searching FTS with k=50 to find all similar chunks...")
    print(f"-" * 70)

    fts_request = {
        "knn": [{
            "field": "embedding",
            "vector": original_embedding,
            "k": 50
        }],
        "size": 50,
        "fields": ["*"]
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request,
            auth=("Administrator", "password123"),
            timeout=30.0
        )

        if response.status_code != 200:
            print(f"❌ FTS request failed: {response.status_code}")
            print(response.text)
            return

        data = response.json()
        hits = data.get('hits', [])

        print(f"\n✓ FTS returned {len(hits)} results")
        print(f"  Total hits: {data.get('total_hits', 0)}")

        # Group by score
        score_groups = {}
        for hit in hits:
            score = hit.get('score', 0.0)
            score_key = f"{score:.4f}"
            if score_key not in score_groups:
                score_groups[score_key] = []
            score_groups[score_key].append(hit)

        print(f"\n  Unique score values: {len(score_groups)}")

        # Show top score group
        if score_groups:
            top_score = sorted(score_groups.keys(), key=lambda x: float(x), reverse=True)[0]
            top_group = score_groups[top_score]

            print(f"\n  Top score: {top_score}")
            print(f"  Chunks with top score: {len(top_group)}")

            # Check if original is in top group
            original_in_top = any(h.get('id') == original_id for h in top_group)
            print(f"  Original chunk in top group? {original_in_top}")

            # Show all IDs in top group
            print(f"\n  Document IDs with score {top_score}:")
            for i, hit in enumerate(top_group, 1):
                hit_id = hit.get('id')
                is_original = hit_id == original_id
                marker = " ← ORIGINAL" if is_original else ""
                print(f"    {i}. {hit_id}{marker}")

        # Check if original appears anywhere in results
        original_rank = None
        for i, hit in enumerate(hits, 1):
            if hit.get('id') == original_id:
                original_rank = i
                break

        print(f"\n" + "=" * 70)
        print(f"RESULT:")
        print(f"=" * 70)
        if original_rank:
            original_score = hits[original_rank - 1].get('score', 0.0)
            print(f"✓ Original chunk found at rank #{original_rank}")
            print(f"  Score: {original_score:.6f}")
        else:
            print(f"❌ Original chunk NOT found in top 50 results!")
            print(f"\nThis suggests:")
            print(f"  1. FTS index may not include this chunk")
            print(f"  2. OR there are 50+ chunks with higher/equal similarity")
            print(f"  3. OR document ID ordering pushes it out of results")

        print("=" * 70)

if __name__ == '__main__':
    asyncio.run(main())
