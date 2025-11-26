#!/usr/bin/env python3
"""
Test N1QL SEARCH() with explicit index name
"""
from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions

db = CouchbaseClient()

# Get a test chunk
query = """
    SELECT META().id, embedding
    FROM `code_kosha`
    WHERE type='code_chunk'
      AND repo_id='kbhalerao/labcore'
      AND file_path LIKE '%consumer%'
    LIMIT 1
"""

result = db.cluster.query(query)
chunks = list(result)
test_chunk = chunks[0]
original_embedding = test_chunk['embedding']

print("=" * 70)
print("TESTING N1QL SEARCH() WITH EXPLICIT INDEX NAME")
print("=" * 70)

# Test 1: SEARCH() without index name (current approach)
print("\nTest 1: SEARCH() without index name:")
print("-" * 70)

n1ql_no_index = """
    SELECT META().id, repo_id, file_path,
           SEARCH_SCORE() as score
    FROM `code_kosha`
    WHERE type = 'code_chunk'
    AND SEARCH(`code_kosha`, {
        "knn": [{
            "field": "embedding",
            "vector": $vector,
            "k": 5
        }]
    })
    ORDER BY score DESC
    LIMIT 5
"""

try:
    result = db.cluster.query(n1ql_no_index, QueryOptions(named_parameters={"vector": original_embedding}))
    results = list(result)
    print(f"Results: {len(results)}")
    if results:
        scores = [r.get('score', 0.0) for r in results]
        print(f"Scores: {scores}")
        print(f"All zeros? {all(s == 0.0 for s in scores)}")
except Exception as e:
    print(f"Error: {e}")

# Test 2: SEARCH() with explicit index parameter
print("\nTest 2: SEARCH() with explicit index parameter:")
print("-" * 70)

n1ql_with_index = """
    SELECT META().id, repo_id, file_path,
           SEARCH_SCORE() as score
    FROM `code_kosha`
    WHERE type = 'code_chunk'
    AND SEARCH(`code_kosha`, {
        "index": "code_vector_index",
        "knn": [{
            "field": "embedding",
            "vector": $vector,
            "k": 5
        }]
    })
    ORDER BY score DESC
    LIMIT 5
"""

try:
    result = db.cluster.query(n1ql_with_index, QueryOptions(named_parameters={"vector": original_embedding}))
    results = list(result)
    print(f"Results: {len(results)}")
    if results:
        scores = [r.get('score', 0.0) for r in results]
        print(f"Scores: {scores}")
        print(f"All zeros? {all(s == 0.0 for s in scores)}")
        print("\nTop 3 results:")
        for i, r in enumerate(results[:3], 1):
            print(f"  {i}. {r.get('repo_id')}/{r.get('file_path')} - Score: {r.get('score', 0.0):.2f}")
except Exception as e:
    print(f"Error: {e}")

print("\n" + "=" * 70)
