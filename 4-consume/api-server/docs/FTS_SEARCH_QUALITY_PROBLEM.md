# FTS Hybrid Search Quality Problem

**Date:** 2025-11-22
**Status:** Root cause identified, partial fix applied

## Problem Statement

Hybrid search (vector kNN + text search) with type filtering returns 0% precision on test queries. Expected code files are not being retrieved, and results are polluted with:
- ✗ Markdown documentation files (`type='document'`)
- ✗ Git commit messages (`type='commit'`)
- ✗ Empty `__init__.py` files

**Test Queries:**
1. "Django Channels background worker with job counter decorator" → Expected: `orders/consumers.py`, `common/consumer_decorators.py`
2. "Svelte 5 component with runes for state management" → Expected: `src/lib/components/chat/ChatInput.svelte`
3. "Redis integration for background job tracking" → Expected: `common/consumer_decorators.py`, `common/redis_lock.py`

**Current Match Rate:** 0/5 (0%)

## Root Cause Analysis

### Investigation Timeline

1. **Initial Symptom:** Search returns markdown files and commits instead of code
2. **Hypothesis 1:** FTS index configuration issue → Disproven (index is correct)
3. **Hypothesis 2:** Filter at wrong level → Tested moving filter inside kNN per Couchbase docs
4. **Discovery:** Filter structure is correct, but kNN pre-filtering doesn't work

### Key Findings

#### Test 1: Text-Only Search with Type Filter
```json
{
  "query": {
    "conjuncts": [
      {"term": "code_chunk", "field": "type"},
      {"match": "job_counter", "field": "content"}
    ]
  }
}
```
**Result:** ✅ 4/4 results are `type='code_chunk'` (100% correct)

#### Test 2: Vector kNN Search with Type Pre-Filter
```json
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 5,
    "filter": {
      "term": "code_chunk",
      "field": "type"
    }
  }]
}
```
**Result:** ✗ 1/5 results are `type='code_chunk'` (20% correct)
- 2 results: `type='commit'`
- 2 results: `type='document'`
- 1 result: `type='code_chunk'`

### Conclusion

**Couchbase FTS kNN pre-filtering is broken for the `type` field.**
- Text-only filtering: Works perfectly ✅
- Vector kNN pre-filtering: Does not filter ✗

The filter is correctly structured according to Couchbase documentation (https://docs.couchbase.com/server/current/vector-search/pre-filtering-vector-search.html), but the actual filtering doesn't happen for kNN queries.

## Current Implementation

### File: `app/chat/manual_rag_agent.py`

#### FTS Query Structure (Lines 98-146)
```python
# Build filter for pre-filtering
filter_conjuncts = []
filter_conjuncts.append({"term": doc_type, "field": "type"})  # type filter
if text_query:
    filter_conjuncts.append({"match": text_query, "field": "content"})
if repo_filter:
    filter_conjuncts.append({"term": repo_filter, "field": "repo_id"})

knn_filter = {"conjuncts": filter_conjuncts} if len(filter_conjuncts) > 1 else filter_conjuncts[0]

# Vector search with pre-filtering (DOESN'T WORK)
fts_request["knn"] = [{
    "field": "embedding",
    "vector": query_embedding,
    "k": code_limit,
    "filter": knn_filter  # Pre-filter inside kNN
}]
```

####  N1QL Post-Filtering (Lines 181-196) - **WORKAROUND**
```python
# Build N1QL WHERE clause
where_clauses = ["META().id IN $doc_ids"]

# Add type filter (defense against broken FTS pre-filtering)
where_clauses.append("type = $doc_type")
query_params["doc_type"] = doc_type

# Add repo filter
if repo_filter:
    where_clauses.append("repo_id = $repo_id")

# Filter out empty __init__.py files
where_clauses.append("LENGTH(content) > 50")
```

## Applied Fixes

### Fix 1: N1QL Type Filtering (✅ Implemented)
Since FTS kNN pre-filtering doesn't work, filter by type in N1QL after retrieval:
- Filters out `type='document'` and `type='commit'`
- Only returns `type='code_chunk'` results
- **Trade-off:** Loses some ranking quality because wrong-type docs consume top-k slots

### Fix 2: Content Length Filter (✅ Implemented)
Filter out very short files to remove empty `__init__.py` pollution:
```python
where_clauses.append("LENGTH(content) > 50")
```
- Removes files with <50 characters
- Helps reduce noise from boilerplate files
- **Trade-off:** May filter out legitimately short but important files

## Remaining Issues

1. **Empty __init__.py Files**
   Still dominate results due to high mutual similarity in vector space

2. **Low Precision**
   Expected files still not appearing in top results

3. **FTS Index Stored Fields**
   Only `content` field is stored; `type`, `repo_id`, `file_path` are indexed but not stored
   → Cannot verify filtering at FTS level, must rely on N1QL

## Next Steps

### Option 1: Exclude __init__.py Entirely
```python
where_clauses.append("file_path NOT LIKE '%__init__.py'")
```

### Option 2: Boost Text Search Weight
Increase BM25 contribution to down-rank similar but irrelevant files

### Option 3: Content-Based Filtering
Filter by additional criteria:
- Minimum number of lines
- Presence of specific code patterns
- Language-specific heuristics

### Option 4: Report to Couchbase
File bug report about kNN pre-filtering not working with type field

## Test Files Created

- `test_fts_type_filter_direct.py` - Tests text-only type filtering (passes)
- `test_knn_type_filter_direct.py` - Tests kNN pre-filtering (fails)
- `test_hybrid_search_quality.py` - End-to-end search quality test
- `check_md_file_types.py` - Verifies markdown files are `type='document'`
- `check_fts_stored_fields.py` - Shows which fields are stored in FTS index

## References

- [Couchbase Vector Search Pre-Filtering](https://docs.couchbase.com/server/current/vector-search/pre-filtering-vector-search.html)
- [FTS Index Configuration](http://localhost:8094/api/index/code_vector_index)
