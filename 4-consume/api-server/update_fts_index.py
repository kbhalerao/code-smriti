#!/usr/bin/env python3
"""
Update FTS index to add missing fields (content, file_path, etc.)
"""
import json
import requests
from requests.auth import HTTPBasicAuth

FTS_URL = "http://localhost:8094/api/index/code_vector_index"
AUTH = HTTPBasicAuth("Administrator", "password123")

print("=" * 70)
print("UPDATING FTS INDEX WITH MISSING FIELDS")
print("=" * 70)

# Get current index definition
print("\n1. Fetching current index definition...")
response = requests.get(FTS_URL, auth=AUTH)
if response.status_code != 200:
    print(f"❌ Failed to fetch index: {response.status_code}")
    exit(1)

index_def = response.json()['indexDef']
print("✓ Current definition fetched")

# Update field mappings
print("\n2. Adding missing fields to type mappings...")

types = index_def['params']['mapping']['types']

# Add content field to ALL types (code_chunk, document, commit)
content_field = {
    "dynamic": False,
    "enabled": True,
    "fields": [
        {
            "analyzer": "standard",  # Standard analyzer for full-text search
            "index": True,
            "name": "content",
            "store": True,  # Store for highlighting in search results
            "type": "text"
        }
    ]
}

# Add file_path field for code_chunk and document
file_path_field = {
    "dynamic": False,
    "enabled": True,
    "fields": [
        {
            "analyzer": "keyword_analyzer",  # Exact matching + case-insensitive
            "index": True,
            "name": "file_path",
            "type": "text"
        }
    ]
}

# Add author field for commits
author_field = {
    "dynamic": False,
    "enabled": True,
    "fields": [
        {
            "analyzer": "keyword_analyzer",
            "index": True,
            "name": "author",
            "type": "text"
        }
    ]
}

# Add commit_hash field for commits
commit_hash_field = {
    "dynamic": False,
    "enabled": True,
    "fields": [
        {
            "analyzer": "keyword_analyzer",
            "index": True,
            "name": "commit_hash",
            "type": "text"
        }
    ]
}

# Update code_chunk type
if 'code_chunk' in types:
    types['code_chunk']['properties']['content'] = content_field
    types['code_chunk']['properties']['file_path'] = file_path_field
    print("  ✓ Added content + file_path to code_chunk")

# Update document type
if 'document' in types:
    types['document']['properties']['content'] = content_field
    types['document']['properties']['file_path'] = file_path_field
    print("  ✓ Added content + file_path to document")

# Update commit type
if 'commit' in types:
    types['commit']['properties']['content'] = content_field
    types['commit']['properties']['author'] = author_field
    types['commit']['properties']['commit_hash'] = commit_hash_field
    print("  ✓ Added content + author + commit_hash to commit")

# Clean up UUID fields before update
if 'uuid' in index_def:
    del index_def['uuid']
if 'sourceUUID' in index_def:
    del index_def['sourceUUID']

print("\n3. Deleting existing index...")
response = requests.delete(FTS_URL, auth=AUTH)
if response.status_code != 200:
    print(f"❌ Failed to delete index: {response.status_code}")
    print(response.text)
    exit(1)
print("✓ Index deleted")

print("\n4. Creating new index with updated fields...")
response = requests.put(
    FTS_URL,
    json=index_def,
    auth=AUTH,
    headers={"Content-Type": "application/json"}
)

if response.status_code == 200:
    print("✓ FTS index created successfully!")
    print("\n⚠️  Index will rebuild. This may take a few minutes.")
    print("   Monitor progress with:")
    print("   curl -s -u Administrator:password123 http://localhost:8094/api/index/code_vector_index/count")
else:
    print(f"❌ Failed to create index: {response.status_code}")
    print(response.text)
    exit(1)

print("\n" + "=" * 70)
print("FIELDS ADDED:")
print("=" * 70)
print("• content (all types) - Enable full-text search on code/docs/commits")
print("• file_path (code_chunk, document) - Filter by file name/path")
print("• author (commit) - Filter commits by developer")
print("• commit_hash (commit) - Lookup specific commits")
print("=" * 70)
