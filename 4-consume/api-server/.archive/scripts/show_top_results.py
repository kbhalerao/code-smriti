#!/usr/bin/env python3
"""
Show top FTS results with repo info
"""
import asyncio
import httpx
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions

async def main():
    db = CouchbaseClient()

    # Get original chunk embedding
    original_id = "0359c38bfd532519a85c9294d9f71307febb1ec14336e0ab7d27dc5e4f767f6d"

    query = "SELECT embedding FROM `code_kosha` WHERE META().id = $id"
    result = db.cluster.query(query, QueryOptions(named_parameters={"id": original_id}))
    chunks = list(result)

    original_embedding = chunks[0]['embedding']

    # Search FTS
    fts_request = {
        "knn": [{
            "field": "embedding",
            "vector": original_embedding,
            "k": 5
        }],
        "size": 5,
        "fields": ["*"]
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://localhost:8094/api/index/code_vector_index/query",
            json=fts_request,
            auth=("Administrator", "password123"),
            timeout=30.0
        )
        hits = response.json().get('hits', [])

        print("=" * 70)
        print("TOP 5 FTS RESULTS - CHECKING THE BET!")
        print("=" * 70)
        print(f"Original chunk from: kbhalerao/labcore")
        print(f"User's bet: Top 2 are from topsoil and jayp-eci")
        print()

        top_repos = []
        for i, hit in enumerate(hits[:5], 1):
            doc_id = hit.get('id')
            score = hit.get('score', 0.0)
            is_original = doc_id == original_id

            # Fetch document from database
            doc_query = "SELECT repo_id, file_path FROM `code_kosha` WHERE META().id = $id"
            doc_result = db.cluster.query(doc_query, QueryOptions(named_parameters={"id": doc_id}))
            docs = list(doc_result)

            if docs:
                doc = docs[0]
                repo = doc.get('repo_id', 'N/A')
                file_path = doc.get('file_path', 'N/A')
                marker = " ← ORIGINAL" if is_original else ""

                print(f"{i}. Score: {score:.6f}{marker}")
                print(f"   Repo: {repo}")
                print(f"   File: {file_path}")

                if i <= 2:
                    top_repos.append(repo)
                print()

        print("=" * 70)
        print("VERDICT:")
        print("=" * 70)
        print(f"Top 2 repos: {top_repos}")

        has_topsoil = any('topsoil' in r.lower() for r in top_repos)
        has_jayp = any('jayp' in r for r in top_repos)

        if has_topsoil and has_jayp:
            print("✓ BET WON! Both topsoil and jayp-eci in top 2!")
        elif has_topsoil or has_jayp:
            print("~ PARTIAL WIN! One of them is in top 2")
        else:
            print("✗ BET LOST! Neither topsoil nor jayp-eci in top 2")

        print("=" * 70)

asyncio.run(main())
