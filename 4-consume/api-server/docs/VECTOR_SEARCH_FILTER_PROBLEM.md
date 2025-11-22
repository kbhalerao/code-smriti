# Vector Search Filter Problem - Technical Specification

**Date**: 2025-11-21
**Status**: 🔴 BLOCKING - Couchbase FTS filters not working with kNN vector search
**Priority**: P0 - Prevents RAG system from returning relevant results

---

## Problem Statement

The Couchbase Full-Text Search (FTS) vector search filters are **not working** when applied to kNN queries. Despite sending correct filter syntax in the request, the search returns results from **all repositories** instead of filtering to the specified `repo_id`.

**Impact**: The RAG system returns code from wrong repositories, making it unusable for production.

---

## System Architecture

### RAG Agent Flow
```
User Query → ManualRAGAgent → search_code_tool() → Couchbase FTS kNN Search → Results
                                                    ↓
                                            Filter by repo_id + type
```

### Technology Stack
- **Database**: Couchbase Server 7.x
- **Search**: Couchbase Full-Text Search (FTS) with vector index
- **Embeddings**: `nomic-ai/nomic-embed-text-v1.5` (768 dimensions)
- **Vector Index**: `code_vector_index`
- **Similarity**: dot_product
- **Documents**: 33,325 indexed (code_chunk, document, commit types)

---

## Current Configuration

### FTS Index Mapping

The `code_vector_index` has been configured with:

1. **Custom keyword analyzer** for exact string matching:
```json
"analysis": {
  "analyzers": {
    "keyword_analyzer": {
      "type": "custom",
      "char_filters": [],
      "tokenizer": "single",
      "token_filters": ["to_lower"]
    }
  }
}
```

2. **Field mappings** for `code_chunk` type:
```json
{
  "type": {
    "enabled": true,
    "fields": [{
      "analyzer": "keyword_analyzer",
      "index": true,
      "name": "type",
      "type": "text"
    }]
  },
  "repo_id": {
    "enabled": true,
    "fields": [{
      "analyzer": "keyword_analyzer",
      "index": true,
      "name": "repo_id",
      "type": "text"
    }]
  },
  "language": {
    "enabled": true,
    "fields": [{
      "analyzer": "keyword_analyzer",
      "index": true,
      "name": "language",
      "type": "text"
    }]
  },
  "embedding": {
    "enabled": true,
    "fields": [{
      "dims": 768,
      "index": true,
      "name": "embedding",
      "similarity": "dot_product",
      "type": "vector",
      "vector_index_optimized_for": "recall"
    }]
  }
}
```

Similar mappings exist for `document` and `commit` types.

---

## Query Syntax Evolution

### ❌ Attempt 1: Query at top level (WRONG)
```json
{
  "query": {
    "conjuncts": [
      {"match": "code_chunk", "field": "type"},
      {"match": "kbhalerao/labcore", "field": "repo_id"}
    ]
  },
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 20
  }]
}
```
**Problem**: Filters and kNN are combined with OR, not AND. Doesn't pre-filter.

### ❌ Attempt 2: Filter inside kNN with match (WRONG)
```json
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 20,
    "filter": {
      "conjuncts": [
        {"match": "code_chunk", "field": "type"},
        {"match": "kbhalerao/labcore", "field": "repo_id"}
      ]
    }
  }]
}
```
**Problem**: `match` query tokenizes even with keyword_analyzer. The `/` in `kbhalerao/labcore` gets tokenized.

### ❌ Attempt 3: match_phrase (WRONG)
```json
{"match_phrase": "kbhalerao/labcore", "field": "repo_id"}
```
**Problem**: Still uses analysis/tokenization. Doesn't work with keyword_analyzer.

### ⚠️ Attempt 4: term query (CURRENT - STILL NOT WORKING)
```json
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 20,
    "filter": {
      "conjuncts": [
        {"term": "code_chunk", "field": "type"},
        {"term": "kbhalerao/labcore", "field": "repo_id"}
      ]
    }
  }]
}
```
**Expected**: Exact match, no analysis
**Actual**: Still returning results from multiple repos

**Note**: The keyword_analyzer has `to_lower` filter, so terms are indexed as lowercase. We added `.lower()` to the query terms, but **filters still don't work**.

---

## Verification Evidence

### Test Query
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "Django Channels background worker with job counter decorator", "stream": false}'
```

### Actual FTS Request Sent
```json
{
  "knn": [
    {
      "field": "embedding",
      "vector": [-0.9012685418128967, 0.8789346814155579, ...],
      "k": 20,
      "filter": {
        "conjuncts": [
          {"term": "code_chunk", "field": "type"},
          {"term": "kbhalerao/labcore", "field": "repo_id"}
        ]
      }
    }
  ],
  "size": 20,
  "fields": ["*"]
}
```

### Actual Results Received
Results from **multiple repositories** despite filter:
- `kbhalerao/labcore` (only 1 result)
- `jayp-eci/labcore`
- `ContinuumAgInc/topsoil2.0`
- `test/code-smriti`
- `kbhalerao/evolvechiro`
- `kbhalerao/farmdoc-insurance`
- `PeoplesCompany/farmworthdb`
- `policy-design-lab/farmdoc-service`
- `kbhalerao/agwx`
- `kbhalerao/slithytoves`
- `kbhalerao/agkit.io-backend`

**Expected**: Only results from `kbhalerao/labcore`

---

## Hypotheses for Why Filter Isn't Working

### Hypothesis 1: keyword_analyzer misconfiguration
- The `to_lower` filter might be interfering
- Consider removing `to_lower` and using case-sensitive matching
- Or use a true keyword field type (if supported)

### Hypothesis 2: term query case sensitivity
- `term` queries are case-sensitive and do NO analysis
- Indexed values are lowercased due to `to_lower` filter
- Query terms need to exactly match indexed form
- **Current code does `.lower()` but still doesn't work**

### Hypothesis 3: Filter placement or syntax error
- Filter might not be processed correctly inside kNN object
- Couchbase version might have a bug
- Need to verify Couchbase version supports filters in kNN

### Hypothesis 4: Field not properly indexed for filtering
- Fields indexed with analyzers might not support term queries
- May need different field type or index configuration
- Possible conflict between vector field and filter fields

### Hypothesis 5: query execution order
- kNN might execute before filter application
- Filter might be applied after kNN ranking (post-filtering, not pre-filtering)
- May need different query structure

---

## What We Know Works

1. ✅ **Vector search works** - returns semantically similar results
2. ✅ **Index is healthy** - 33,325 documents indexed
3. ✅ **Embeddings are correct** - using same model for indexing and querying
4. ✅ **Field values exist** - queries return documents with `repo_id` field populated
5. ✅ **Filter syntax is valid** - FTS accepts the request without errors
6. ✅ **keyword_analyzer exists** - custom analyzer is in index configuration

---

## What We Need to Fix

**Primary Goal**: Make Couchbase FTS pre-filter vector search results by `repo_id` and `type` fields.

**Success Criteria**:
- Query with filter `repo_id = "kbhalerao/labcore"` returns ONLY results from that repo
- Filter applied BEFORE kNN ranking (pre-filtering, not post-filtering)
- Performance acceptable (< 1 second for 20 results)

---

## Code Location

**File**: `/Users/kaustubh/Documents/code/code-smriti/4-consume/api-server/app/chat/manual_rag_agent.py`

**Function**: `search_code_tool()` (lines 59-203)

**Relevant Code**:
```python
# Generate query embedding
query_with_prefix = f"search_document: {query}"
query_embedding = ctx.embedding_model.encode(query_with_prefix).tolist()

# Build filter
code_filter = {
    "conjuncts": [
        {"term": "code_chunk", "field": "type"},
        {"term": "kbhalerao/labcore".lower(), "field": "repo_id"}
    ]
}

# Build FTS request
code_search_request = {
    "knn": [{
        "field": "embedding",
        "vector": query_embedding,
        "k": code_limit,
        "filter": code_filter  # Filter INSIDE knn object
    }],
    "size": code_limit,
    "fields": ["*"]
}

# Call Couchbase FTS
response = await ctx.http_client.post(
    f"http://localhost:8094/api/index/code_vector_index/query",
    json=code_search_request,
    auth=(couchbase_user, couchbase_pass)
)
```

---

## Documentation References

1. **Couchbase Vector Search Pre-filtering**: https://docs.couchbase.com/server/current/vector-search/pre-filtering-vector-search.html
2. **Term Query**: https://docs.couchbase.com/server/current/fts/fts-supported-queries-term.html
3. **Query Types**: https://docs.couchbase.com/server/current/fts/fts-query-types.html
4. **Vector Search with REST API**: https://docs.couchbase.com/server/current/vector-search/run-vector-search-rest-api.html

---

## Questions to Investigate

1. **Does Couchbase FTS support pre-filtering with term queries inside kNN objects?**
   - Documentation suggests yes, but need confirmation
   - May need different query structure

2. **Should we use a different field type for filtering?**
   - Consider using N1QL queries instead of FTS REST API?
   - Use hybrid approach with FTS and N1QL?

3. **Is there a Couchbase version requirement?**
   - Check if our version supports kNN filtering
   - Verify if there are known bugs

4. **Should we use a different analyzer or no analyzer?**
   - Try removing `to_lower` filter from keyword_analyzer
   - Try using standard field without custom analyzer

5. **Is there an alternative query syntax?**
   - Try different filter placement
   - Try different query types (prefix, wildcard?)

---

## Next Steps (Recommended Approach)

### Step 1: Verify Couchbase version and capabilities
```bash
curl -u Administrator:password123 http://localhost:8091/pools/default
```
Check version and confirm kNN filter support.

### Step 2: Test filter in isolation (without kNN)
```bash
curl -X POST http://localhost:8094/api/index/code_vector_index/query \
  -u Administrator:password123 \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "conjuncts": [
        {"term": "code_chunk", "field": "type"},
        {"term": "kbhalerao/labcore", "field": "repo_id"}
      ]
    },
    "size": 10
  }'
```
Verify that term queries work for exact matching on these fields.

### Step 3: Test kNN without filter
```bash
# Test that kNN search works
curl -X POST http://localhost:8094/api/index/code_vector_index/query \
  -u Administrator:password123 \
  -H "Content-Type: application/json" \
  -d '{
    "knn": [{
      "field": "embedding",
      "vector": [0.1, 0.2, ...],  # sample vector
      "k": 10
    }]
  }'
```

### Step 4: Try alternative filter syntax
Based on Couchbase examples, try:
```json
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 20,
    "filter": {
      "field": "repo_id",
      "match": "kbhalerao/labcore"  # Try without conjuncts
    }
  }]
}
```

### Step 5: Consider N1QL alternative
If FTS filtering doesn't work, use N1QL with SEARCH() function:
```sql
SELECT META().id, repo_id, file_path, code_text
FROM `code_kosha`
WHERE type = 'code_chunk'
  AND repo_id = 'kbhalerao/labcore'
  AND SEARCH(code_kosha, {
    "knn": [{
      "field": "embedding",
      "vector": [...],
      "k": 20
    }]
  })
LIMIT 20
```

---

## Environment Details

- **OS**: macOS (Darwin 24.6.0)
- **Couchbase**: Running on localhost:8091
- **FTS Port**: 8094
- **Python**: 3.12
- **API Framework**: FastAPI with uvicorn
- **Embedding Model**: sentence-transformers with nomic-embed-text-v1.5

---

## Test Data

**Repository**: `kbhalerao/labcore`
**Expected Files**:
- `orders/consumers.py` (contains `BackgroundOrderPDFGenerator`, `BackgroundWorkOrderBatchGenerator`)
- `common/consumer_decorators.py` (contains `@count_jobs_in_queue` decorator)

**Test Query**: "Django Channels background worker with job counter decorator"

**Current Result**: Returns code from wrong repositories (farmworthdb, farmdoc-service, etc.)
**Expected Result**: Returns code ONLY from `kbhalerao/labcore`

---

## Additional Context

### Why This Matters

The RAG system quality is currently **21.6% good responses** (8/37 questions). The primary issue is:

1. **Semantic search finds documentation instead of code** (78.4% generic responses)
2. **Vector search returns wrong repositories** (filters don't work)
3. **Cannot enforce business logic** (user permissions, repo access control)

Fixing the filter issue will allow us to:
- ✅ Filter to specific repositories (multi-tenancy, permissions)
- ✅ Filter to code chunks only (exclude docs/commits when needed)
- ✅ Filter by language (Python, JavaScript, etc.)
- ✅ Improve response quality by limiting search space

---

## Success Metrics (Post-Fix)

After fixing filters, expect:
- **Filter accuracy**: 100% of results match filter criteria
- **Response quality**: >60% responses contain actual code from specified repo
- **Performance**: <1 second per query
- **Flexibility**: Can filter by repo_id, type, language, or combinations

---

## Contact

This problem statement prepared for: Next LLM session
Original investigation by: Claude (session 2025-11-21)
Files changed during investigation:
- `app/chat/manual_rag_agent.py` (filter syntax iterations)
- FTS index configuration (added keyword_analyzer, commit type)
