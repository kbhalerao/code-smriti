"""
Simple vector search test with current (partially re-embedded) data
"""

from sentence_transformers import SentenceTransformer
from app.database.couchbase_client import CouchbaseClient

# Load model
print("Loading nomic-ai model...")
model = SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True)
print(f"✓ Model loaded")

# Connect to DB
db = CouchbaseClient()
print("✓ Connected to Couchbase\n")

# Test queries
test_queries = [
    "Django Channels background worker",
    "React useEffect hook",
    "Python async function"
]

for query_text in test_queries:
    print(f"\n{'='*80}")
    print(f"Query: {query_text}")
    print('='*80)

    # Generate embedding
    query_embedding = model.encode(
        f"search_query: {query_text}",
        normalize_embeddings=True
    )

    # Try N1QL vector search directly
    # Use KNN function in N1QL
    import json
    query_vector_json = json.dumps(query_embedding.tolist())

    n1ql_query = f"""
    SELECT META().id as doc_id,
           file_path,
           repo_id,
           type,
           SUBSTR(code_text, 0, 150) as preview
    FROM `code_kosha`
    WHERE repo_id IS NOT NULL
      AND embedding IS NOT NULL
    ORDER BY APPROX_VECTOR_DISTANCE(embedding, {query_vector_json})
    LIMIT 5
    """

    try:
        result = db.cluster.query(n1ql_query)
        rows = list(result)

        if rows:
            for i, row in enumerate(rows, 1):
                print(f"\n[{i}] {row.get('file_path', 'unknown')}")
                print(f"    Repo: {row.get('repo_id', 'unknown')}")
                print(f"    Type: {row.get('type', 'unknown')}")
                preview = row.get('preview', '')[:100]
                if preview:
                    print(f"    Preview: {preview}...")
        else:
            print("  No results found")

    except Exception as e:
        print(f"  ERROR: {e}")

print(f"\n\n{'='*80}")
print("Test Complete")
print('='*80)
