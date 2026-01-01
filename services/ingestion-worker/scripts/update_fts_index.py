#!/usr/bin/env python3
"""Add repo_bdr type mapping to FTS index."""

import httpx
import os
import json
from pathlib import Path
from dotenv import load_dotenv

# Load env from repo root
repo_root = Path(__file__).parent.parent.parent.parent
load_dotenv(repo_root / ".env")

password = os.environ['COUCHBASE_PASSWORD']

# Get current index
resp = httpx.get(
    'http://localhost:8094/api/index',
    auth=('Administrator', password)
)
resp.raise_for_status()
data = resp.json()
index_def = data['indexDefs']['indexDefs']['code_vector_index']

# Always apply mapping to ensure consistent index state
existing_types = list(index_def['params']['mapping']['types'].keys())
print('Existing types:', existing_types)

# Add repo_bdr type mapping (with store=true for repo_id)
repo_bdr_mapping = {
    'dynamic': False,
    'enabled': True,
    'properties': {
        'content': {
            'dynamic': False,
            'enabled': True,
            'fields': [
                {'analyzer': 'standard', 'index': True, 'name': 'content', 'store': True, 'type': 'text'}
            ]
        },
        'embedding': {
            'dynamic': False,
            'enabled': True,
            'fields': [
                {'dims': 768, 'index': True, 'name': 'embedding', 'similarity': 'dot_product', 'type': 'vector', 'vector_index_optimized_for': 'recall'}
            ]
        },
        'repo_id': {
            'dynamic': False,
            'enabled': True,
            'fields': [
                {'analyzer': 'keyword_analyzer', 'index': True, 'name': 'repo_id', 'store': True, 'type': 'text'}
            ]
        },
        'type': {
            'dynamic': False,
            'enabled': True,
            'fields': [
                {'analyzer': 'keyword_analyzer', 'index': True, 'name': 'type', 'type': 'text'}
            ]
        }
    }
}

index_def['params']['mapping']['types']['repo_bdr'] = repo_bdr_mapping
print('Types after adding repo_bdr:', list(index_def['params']['mapping']['types'].keys()))

# Update index - use PUT to /api/index/{name}
resp2 = httpx.put(
    'http://localhost:8094/api/index/code_vector_index',
    auth=('Administrator', password),
    json=index_def,
    timeout=120.0
)
print(f'Update status: {resp2.status_code}')
if resp2.status_code != 200:
    print(resp2.text)
else:
    print('Successfully updated FTS index with repo_bdr mapping')
