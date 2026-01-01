# Couchbase FTS Vector Search: Lessons Learned

This document captures findings from debugging FTS vector search issues, specifically around hybrid search ranking and stored field retrieval.

## Problem 1: Flat Ranking Scores

**Symptom**: All search results had identical scores (7.7010) regardless of semantic relevance.

**Root Cause**: Using `query` + `knn_operator: "and"` causes the term query score to dominate vector similarity.

```json
// WRONG - term query score dominates
{
  "query": {"term": "repo_bdr", "field": "type"},
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 10
  }],
  "knn_operator": "and"
}
```

The term query contributes a BM25-style score (~7.7) that's added to the vector similarity score (~0.7), completely drowning out the semantic relevance.

**Solution**: Move the filter **inside** the `knn` object for pre-filtering:

```json
// CORRECT - pure vector similarity ranking
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 10,
    "filter": {"term": "repo_bdr", "field": "type"}
  }],
  "fields": ["content", "repo_id"],
  "size": 10
}
```

With pre-filtering, the filter is applied **before** vector search, so scores reflect pure vector similarity (0.73, 0.68, 0.65...).

## Problem 2: Missing Stored Fields

**Symptom**: FTS results returned `content` but not `repo_id`, even though the mapping had `"store": true`.

**Root Cause**: Documents were indexed **before** `store: true` was added to the mapping. FTS only stores field values for documents indexed after the mapping is updated.

**Solution**: Delete and recreate the index to force a full reindex:

```bash
# Delete existing index
curl -X DELETE -u "Administrator:$PASSWORD" \
  "http://localhost:8094/api/index/code_vector_index"

# Create new index with full mapping
curl -X PUT -u "Administrator:$PASSWORD" \
  -H "Content-Type: application/json" \
  "http://localhost:8094/api/index/code_vector_index" \
  -d @fts_index_definition.json
```

The reindex is fast (~10 seconds for 130K documents on local SSD).

## FTS Index Mapping Reference

Key settings for each field:

```json
{
  "properties": {
    "content": {
      "fields": [{
        "name": "content",
        "type": "text",
        "analyzer": "standard",
        "index": true,
        "store": true  // Required to return in results
      }]
    },
    "embedding": {
      "fields": [{
        "name": "embedding",
        "type": "vector",
        "dims": 768,
        "similarity": "dot_product",
        "index": true,
        "vector_index_optimized_for": "recall"
      }]
    },
    "repo_id": {
      "fields": [{
        "name": "repo_id",
        "type": "text",
        "analyzer": "keyword_analyzer",  // Exact match
        "index": true,
        "store": true
      }]
    },
    "type": {
      "fields": [{
        "name": "type",
        "type": "text",
        "analyzer": "keyword_analyzer",
        "index": true
        // No store - only used for filtering
      }]
    }
  }
}
```

## Hybrid Search Strategies

### Strategy 1: Pre-filtered KNN (Recommended for Type Filtering)

Best when you want pure semantic ranking within a document type:

```python
fts_request = {
    "knn": [{
        "field": "embedding",
        "vector": query_embedding,
        "k": limit,
        "filter": {"term": doc_type, "field": "type"}
    }],
    "fields": ["content", "repo_id"],
    "size": limit
}
```

### Strategy 2: Multiple KNN Searches

For combining results from multiple document types with equal weight:

```python
for doc_type in ["repo_bdr", "document"]:
    fts_request = {
        "knn": [{
            "field": "embedding",
            "vector": query_embedding,
            "k": limit,
            "filter": {"term": doc_type, "field": "type"}
        }],
        "fields": ["content", "repo_id", "file_path", "type"],
        "size": limit
    }
    # Merge results...
```

### Strategy 3: True Hybrid (BM25 + KNN)

When you need keyword boosting alongside semantic search:

```python
fts_request = {
    "query": {
        "match": keyword_query,
        "field": "content"
    },
    "knn": [{
        "field": "embedding",
        "vector": query_embedding,
        "k": limit
    }],
    "knn_operator": "or"  # Combine scores
}
```

Use carefully - the BM25 scores can still dominate. Consider normalizing scores before combining.

## Testing FTS Queries

### Quick test with Python (handles auth properly)

```bash
cd services/ingestion-worker && uv run python -c "
import httpx
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path('/path/to/repo/.env'))
password = os.environ['COUCHBASE_PASSWORD']

resp = httpx.post(
    'http://localhost:8094/api/index/code_vector_index/query',
    auth=('Administrator', password),
    json={
        'query': {'term': 'repo_bdr', 'field': 'type'},
        'fields': ['content', 'repo_id'],
        'size': 3
    }
)
for hit in resp.json().get('hits', []):
    print(hit.get('fields', {}))
"
```

### Check index document count

```bash
curl -s -u "Administrator:$COUCHBASE_PASSWORD" \
  "http://localhost:8094/api/index/code_vector_index/count"
```

## Environment Notes

- **Docker containers** need `COUCHBASE_HOST` env var (not hardcoded localhost)
- **Couchbase user** is `Administrator` (not configurable in dev setup)
- **Password** is in `.env` as `COUCHBASE_PASSWORD`
- **FTS port** is 8094 (not the query service port 8093)

## References

- [Couchbase Vector Search](https://docs.couchbase.com/server/current/fts/fts-vector-search.html)
- V4_DESIGN.md - Embedding normalization requirements
- `services/api-server/app/chat/routes.py` - ask_agsci implementation
