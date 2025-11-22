#!/usr/bin/env python3
"""
Quick test of subdocument API for normalization - fail fast!
"""
import numpy as np
from app.database.couchbase_client import CouchbaseClient
from couchbase import subdocument

db = CouchbaseClient()
bucket = db.cluster.bucket("code_kosha")
collection = bucket.default_collection()

# Get one document
n1ql = """
    SELECT META().id, embedding
    FROM `code_kosha`
    WHERE embedding IS NOT MISSING
    LIMIT 1
"""

result = db.cluster.query(n1ql)
doc = list(result)[0]

doc_id = doc['id']
embedding = np.array(doc['embedding'])
norm = np.linalg.norm(embedding)

print(f"Testing with doc: {doc_id}")
print(f"Original norm: {norm:.4f}")

# Normalize
normalized = embedding / norm
normalized_list = normalized.tolist()

print(f"Normalized norm: {np.linalg.norm(normalized):.4f}")

# Try to update
print("Attempting subdocument update...")
try:
    result = collection.mutate_in(
        doc_id,
        [subdocument.upsert("embedding", normalized_list)]
    )
    print(f"✅ Success! Result: {result}")
except Exception as e:
    print(f"❌ Failed: {type(e).__name__}: {e}")

    # Try alternative: replace whole document
    print("\nTrying alternative: fetch and replace whole document...")
    try:
        get_result = collection.get(doc_id)
        doc_content = get_result.content_as[dict]
        doc_content['embedding'] = normalized_list

        replace_result = collection.replace(doc_id, doc_content)
        print(f"✅ Replace succeeded! Result: {replace_result}")
    except Exception as e2:
        print(f"❌ Replace also failed: {type(e2).__name__}: {e2}")

# Verify
print("\nVerifying...")
verify_result = collection.get(doc_id)
verify_doc = verify_result.content_as[dict]
verify_emb = np.array(verify_doc['embedding'])
verify_norm = np.linalg.norm(verify_emb)
print(f"Final norm: {verify_norm:.4f}")

if abs(verify_norm - 1.0) < 0.01:
    print("✅ Normalization verified!")
else:
    print(f"❌ Normalization failed - expected 1.0, got {verify_norm:.4f}")
