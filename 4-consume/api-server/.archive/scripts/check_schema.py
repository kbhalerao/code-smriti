#!/usr/bin/env python3
"""
Check actual document schema in the database
"""
from app.database.couchbase_client import CouchbaseClient
import json

db = CouchbaseClient()

print("=" * 70)
print("CHECKING DOCUMENT SCHEMA")
print("=" * 70)

# Get sample documents for each type
for doc_type in ["code_chunk", "document", "commit"]:
    print(f"\n{doc_type.upper()}:")
    print("-" * 70)

    query = f"""
        SELECT *
        FROM `code_kosha`
        WHERE type = '{doc_type}'
        LIMIT 1
    """

    result = db.cluster.query(query)
    docs = list(result)

    if docs:
        doc = docs[0]['code_kosha']

        # Show all fields (excluding embedding to save space)
        fields = {}
        for key, value in doc.items():
            if key == 'embedding':
                fields[key] = f"<{len(value)} dimensions>"
            else:
                fields[key] = type(value).__name__

        print(json.dumps(fields, indent=2, sort_keys=True))

        # Show sample data for first few fields
        print("\nSample values:")
        for key, value in sorted(doc.items())[:8]:
            if key != 'embedding':
                val_str = str(value)[:100]
                print(f"  {key}: {val_str}")
    else:
        print(f"  No documents found for type={doc_type}")

print("\n" + "=" * 70)
