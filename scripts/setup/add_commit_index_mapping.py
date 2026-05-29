#!/usr/bin/env python3
"""Add commit_index type mapping to the FTS index.

The index historically carried a `commit` type mapping, but commit documents
are stored with `type = "commit_index"` (see backfill_commits.py and the
incremental updater). FTS routes documents to mappings by the `type` field, so
the `commit` mapping never matched any document and commit messages were absent
from the vector index. This migration adds the correctly-named `commit_index`
mapping and drops the dead `commit` mapping.

Run from anywhere:
    uv run python scripts/setup/add_commit_index_mapping.py
"""

import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

repo_root = Path(__file__).parent.parent.parent
load_dotenv(repo_root / ".env")

password = os.environ["COUCHBASE_PASSWORD"]
FTS = "http://localhost:8094/api/index"

# Fetch live index definition (preserves mappings added out-of-band, e.g. repo_bdr).
resp = httpx.get(FTS, auth=("Administrator", password), timeout=30)
resp.raise_for_status()
index_def = resp.json()["indexDefs"]["indexDefs"]["code_vector_index"]
types = index_def["params"]["mapping"]["types"]
print("Existing types:", sorted(types.keys()))

# commit_index mirrors the commit mapping: searchable content + 768-dim vector,
# plus author / commit_hash / repo_id keyword fields for filtering.
commit_index_mapping = {
    "dynamic": False,
    "enabled": True,
    "properties": {
        "author": {"dynamic": False, "enabled": True, "fields": [
            {"analyzer": "keyword_analyzer", "index": True, "name": "author", "type": "text"}]},
        "commit_hash": {"dynamic": False, "enabled": True, "fields": [
            {"analyzer": "keyword_analyzer", "index": True, "name": "commit_hash", "type": "text"}]},
        "content": {"dynamic": False, "enabled": True, "fields": [
            {"analyzer": "standard", "index": True, "name": "content", "store": True, "type": "text"}]},
        "embedding": {"dynamic": False, "enabled": True, "fields": [
            {"dims": 768, "index": True, "name": "embedding", "similarity": "dot_product",
             "type": "vector", "vector_index_optimized_for": "recall"}]},
        "repo_id": {"dynamic": False, "enabled": True, "fields": [
            {"analyzer": "keyword_analyzer", "index": True, "name": "repo_id", "type": "text"}]},
        "type": {"dynamic": False, "enabled": True, "fields": [
            {"analyzer": "keyword_analyzer", "index": True, "name": "type", "type": "text"}]},
    },
}

types["commit_index"] = commit_index_mapping
types.pop("commit", None)  # drop the dead, never-matched mapping
print("Types after migration:", sorted(types.keys()))

resp2 = httpx.put(
    f"{FTS}/code_vector_index",
    auth=("Administrator", password),
    json=index_def,
    timeout=120.0,
)
print("Update status:", resp2.status_code)
if resp2.status_code != 200:
    print(resp2.text)
    raise SystemExit(1)
print("Successfully added commit_index mapping (commit messages are now FTS-indexed).")
