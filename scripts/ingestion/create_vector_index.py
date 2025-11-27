#!/usr/bin/env python3
"""
Create Couchbase Vector Search Index
Sets up HNSW index for fast vector similarity search
"""

import requests
import json
from requests.auth import HTTPBasicAuth

# Couchbase configuration
COUCHBASE_HOST = "localhost"
COUCHBASE_PORT = 8094  # FTS service port
COUCHBASE_USER = "Administrator"
COUCHBASE_PASSWORD = "password123"
BUCKET_NAME = "code_kosha"
INDEX_NAME = "code_vector_index"

# Vector index definition
index_definition = {
    "name": INDEX_NAME,
    "type": "fulltext-index",
    "sourceType": "couchbase",
    "sourceName": BUCKET_NAME,
    "planParams": {
        "maxPartitionsPerPIndex": 1024,
        "indexPartitions": 1
    },
    "params": {
        "doc_config": {
            "docid_prefix_delim": "",
            "docid_regexp": "",
            "mode": "type_field",
            "type_field": "type"
        },
        "mapping": {
            "default_analyzer": "standard",
            "default_datetime_parser": "dateTimeOptional",
            "default_field": "_all",
            "default_mapping": {
                "dynamic": False,
                "enabled": False
            },
            "default_type": "_default",
            "docvalues_dynamic": False,
            "index_dynamic": False,
            "store_dynamic": False,
            "type_field": "type",
            "types": {
                "code_chunk": {
                    "dynamic": False,
                    "enabled": True,
                    "properties": {
                        "embedding": {
                            "enabled": True,
                            "dynamic": False,
                            "fields": [
                                {
                                    "dims": 768,
                                    "index": True,
                                    "name": "embedding",
                                    "similarity": "dot_product",
                                    "type": "vector",
                                    "vector_index_optimized_for": "recall"
                                }
                            ]
                        },
                        "repo_id": {
                            "enabled": True,
                            "dynamic": False,
                            "fields": [
                                {
                                    "index": True,
                                    "name": "repo_id",
                                    "type": "text"
                                }
                            ]
                        },
                        "language": {
                            "enabled": True,
                            "dynamic": False,
                            "fields": [
                                {
                                    "index": True,
                                    "name": "language",
                                    "type": "text"
                                }
                            ]
                        },
                        "chunk_type": {
                            "enabled": True,
                            "dynamic": False,
                            "fields": [
                                {
                                    "index": True,
                                    "name": "chunk_type",
                                    "type": "text"
                                }
                            ]
                        }
                    }
                },
                "document": {
                    "dynamic": False,
                    "enabled": True,
                    "properties": {
                        "embedding": {
                            "enabled": True,
                            "dynamic": False,
                            "fields": [
                                {
                                    "dims": 768,
                                    "index": True,
                                    "name": "embedding",
                                    "similarity": "dot_product",
                                    "type": "vector",
                                    "vector_index_optimized_for": "recall"
                                }
                            ]
                        },
                        "repo_id": {
                            "enabled": True,
                            "dynamic": False,
                            "fields": [
                                {
                                    "index": True,
                                    "name": "repo_id",
                                    "type": "text"
                                }
                            ]
                        },
                        "doc_type": {
                            "enabled": True,
                            "dynamic": False,
                            "fields": [
                                {
                                    "index": True,
                                    "name": "doc_type",
                                    "type": "text"
                                }
                            ]
                        }
                    }
                }
            }
        },
        "store": {
            "indexType": "scorch",
            "segmentVersion": 16
        }
    }
}

def create_index():
    """Create the vector search index"""
    url = f"http://{COUCHBASE_HOST}:{COUCHBASE_PORT}/api/index/{INDEX_NAME}"
    auth = HTTPBasicAuth(COUCHBASE_USER, COUCHBASE_PASSWORD)

    print("="*70)
    print("Creating Couchbase Vector Search Index")
    print("="*70)
    print(f"\nIndex name: {INDEX_NAME}")
    print(f"Bucket: {BUCKET_NAME}")
    print(f"Vector dimensions: 768")
    print(f"Similarity: dot_product (cosine similarity)")
    print(f"Algorithm: HNSW (Hierarchical Navigable Small World)")
    print()

    # Check if index already exists
    check_url = f"http://{COUCHBASE_HOST}:{COUCHBASE_PORT}/api/index/{INDEX_NAME}"
    response = requests.get(check_url, auth=auth)

    if response.status_code == 200:
        print(f"⚠ Index '{INDEX_NAME}' already exists")
        print("\nDo you want to:")
        print("  1. Delete and recreate (recommended if schema changed)")
        print("  2. Keep existing index")
        choice = input("\nChoice (1/2): ").strip()

        if choice == "1":
            print(f"\nDeleting existing index '{INDEX_NAME}'...")
            delete_response = requests.delete(check_url, auth=auth)
            if delete_response.status_code == 200:
                print("✓ Index deleted")
            else:
                print(f"✗ Failed to delete: {delete_response.text}")
                return False
        else:
            print("Keeping existing index")
            return True

    # Create the index
    print(f"Creating index '{INDEX_NAME}'...")
    response = requests.put(
        url,
        json=index_definition,
        auth=auth,
        headers={"Content-Type": "application/json"}
    )

    if response.status_code in [200, 201]:
        print("✓ Index created successfully!")
        print("\n" + "="*70)
        print("Index Build Status")
        print("="*70)
        print("\nThe index is now building in the background.")
        print("This will take ~1-2 minutes for 33K documents.")
        print("\nTo check build progress:")
        print(f"  curl -u {COUCHBASE_USER}:{COUCHBASE_PASSWORD} http://{COUCHBASE_HOST}:{COUCHBASE_PORT}/api/index/{INDEX_NAME}/count")
        print("\nOnce complete, you can query with:")
        print(f"  curl -u {COUCHBASE_USER}:{COUCHBASE_PASSWORD} http://{COUCHBASE_HOST}:{COUCHBASE_PORT}/api/index/{INDEX_NAME}/query")
        print()
        return True
    else:
        print(f"✗ Failed to create index")
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")
        return False

def check_index_status():
    """Check the build status of the index"""
    url = f"http://{COUCHBASE_HOST}:{COUCHBASE_PORT}/api/index/{INDEX_NAME}/count"
    auth = HTTPBasicAuth(COUCHBASE_USER, COUCHBASE_PASSWORD)

    print("\nChecking index status...")
    response = requests.get(url, auth=auth)

    if response.status_code == 200:
        data = response.json()
        count = data.get("count", 0)
        print(f"✓ Index contains {count:,} documents")
        return True
    else:
        print(f"Index not ready yet (status: {response.status_code})")
        return False

if __name__ == "__main__":
    if create_index():
        print("\n" + "="*70)
        print("Waiting for index to build...")
        print("="*70)
        import time
        for i in range(12):  # Wait up to 2 minutes
            time.sleep(10)
            print(f"\nCheck {i+1}/12...")
            if check_index_status():
                print("\n✓ Vector search index is ready!")
                break
        else:
            print("\n⚠ Index still building. Check status manually with:")
            print(f"  curl -u {COUCHBASE_USER}:{COUCHBASE_PASSWORD} http://{COUCHBASE_HOST}:{COUCHBASE_PORT}/api/index/{INDEX_NAME}/count")
