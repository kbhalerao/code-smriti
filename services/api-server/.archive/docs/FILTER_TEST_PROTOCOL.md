# Filter Test Protocol - Quick Reference

**Purpose**: Step-by-step commands to test and verify Couchbase FTS filter fixes

---

## Prerequisites

```bash
# Ensure Couchbase is running
curl -s http://localhost:8091/pools/default | grep version

# Ensure API server is running
curl -s http://localhost:8000/health || echo "Server not running"

# Set credentials
export CB_USER="Administrator"
export CB_PASS="password123"
export CB_HOST="localhost"
```

---

## Test 1: Verify Index Configuration

### Check current analyzer configuration
```bash
curl -s -u $CB_USER:$CB_PASS \
  http://$CB_HOST:8094/api/index/code_vector_index | \
  python3 -c "import sys, json; data=json.load(sys.stdin); print(json.dumps(data['indexDef']['params']['mapping']['analysis'], indent=2))"
```

**Expected**: Should show `keyword_analyzer` with `single` tokenizer and `to_lower` filter.

### Check field mappings
```bash
curl -s -u $CB_USER:$CB_PASS \
  http://$CB_HOST:8094/api/index/code_vector_index | \
  python3 -c "import sys, json; data=json.load(sys.stdin); code_chunk=data['indexDef']['params']['mapping']['types']['code_chunk']; print(json.dumps(code_chunk['properties']['repo_id'], indent=2))"
```

**Expected**: Should show `analyzer: keyword_analyzer` on `repo_id` field.

---

## Test 2: Query Without Filters (Baseline)

### Test basic term query (no kNN)
```bash
curl -X POST http://$CB_HOST:8094/api/index/code_vector_index/query \
  -u $CB_USER:$CB_PASS \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "term": "kbhalerao/labcore",
      "field": "repo_id"
    },
    "size": 5,
    "fields": ["repo_id", "file_path", "type"]
  }' | python3 -c "import sys, json; data=json.load(sys.stdin); print('Total hits:', data.get('total_hits', 0)); [print(f\"  - {h['fields']['repo_id'][0]}: {h['fields'].get('file_path', [''])[0]}\") for h in data.get('hits', [])]"
```

**Expected**: ALL results should have `repo_id = "kbhalerao/labcore"`

**If this fails**: The problem is with the term query itself, not kNN filtering.

### Test with lowercase term
```bash
curl -X POST http://$CB_HOST:8094/api/index/code_vector_index/query \
  -u $CB_USER:$CB_PASS \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "term": "kbhalerao/labcore",
      "field": "repo_id"
    },
    "size": 5
  }' | python3 -c "import sys, json; data=json.load(sys.stdin); print('Lowercase term hits:', data.get('total_hits', 0))"
```

**Expected**: Should return results (keyword_analyzer uses to_lower)

### Test with mixed case term
```bash
curl -X POST http://$CB_HOST:8094/api/index/code_vector_index/query \
  -u $CB_USER:$CB_PASS \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "term": "kbhalerao/Labcore",
      "field": "repo_id"
    },
    "size": 5
  }' | python3 -c "import sys, json; data=json.load(sys.stdin); print('Mixed case term hits:', data.get('total_hits', 0))"
```

**Expected**: Should return 0 results (term query is case-sensitive)

**Diagnosis**: If lowercase works but mixed case doesn't, confirms keyword_analyzer lowercases at index time.

---

## Test 3: kNN Without Filters

### Generate a sample embedding vector
```python
from sentence_transformers import SentenceTransformer
model = SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)
query_embedding = model.encode("search_document: Django Channels background worker").tolist()
print(query_embedding[:10])  # Print first 10 dimensions for testing
```

Save full vector to a file:
```bash
cat > /tmp/test_vector.json <<'EOF'
{
  "knn": [{
    "field": "embedding",
    "vector": [<PASTE_VECTOR_HERE>],
    "k": 10
  }],
  "size": 10,
  "fields": ["repo_id", "file_path", "type", "score"]
}
EOF
```

### Test kNN search
```bash
curl -X POST http://$CB_HOST:8094/api/index/code_vector_index/query \
  -u $CB_USER:$CB_PASS \
  -H "Content-Type: application/json" \
  -d @/tmp/test_vector.json | \
  python3 -c "import sys, json; data=json.load(sys.stdin); print('kNN hits:', len(data.get('hits', []))); [print(f\"  {i+1}. {h['fields']['repo_id'][0]}: {h['fields'].get('file_path', [''])[0]} (score: {h.get('score', 0):.2f})\") for i, h in enumerate(data.get('hits', []))]"
```

**Expected**: Returns 10 results from various repos, ranked by vector similarity.

**Count unique repos**:
```bash
curl -s -X POST http://$CB_HOST:8094/api/index/code_vector_index/query \
  -u $CB_USER:$CB_PASS \
  -H "Content-Type: application/json" \
  -d @/tmp/test_vector.json | \
  python3 -c "import sys, json; data=json.load(sys.stdin); repos = [h['fields']['repo_id'][0] for h in data.get('hits', [])]; print('Unique repos:', len(set(repos))); [print(f\"  - {r}\") for r in sorted(set(repos))]"
```

---

## Test 4: kNN WITH Filters (The Problem)

### Create filtered kNN query
```bash
cat > /tmp/filtered_vector.json <<'EOF'
{
  "knn": [{
    "field": "embedding",
    "vector": [<PASTE_VECTOR_HERE>],
    "k": 20,
    "filter": {
      "conjuncts": [
        {"term": "code_chunk", "field": "type"},
        {"term": "kbhalerao/labcore", "field": "repo_id"}
      ]
    }
  }],
  "size": 20,
  "fields": ["repo_id", "file_path", "type", "language"]
}
EOF
```

### Test filtered kNN
```bash
curl -X POST http://$CB_HOST:8094/api/index/code_vector_index/query \
  -u $CB_USER:$CB_PASS \
  -H "Content-Type: application/json" \
  -d @/tmp/filtered_vector.json | \
  python3 -c "import sys, json; data=json.load(sys.stdin); hits = data.get('hits', []); print(f'Total hits: {len(hits)}'); repos = [h['fields']['repo_id'][0] for h in hits]; unique = set(repos); print(f'Unique repos: {len(unique)}'); labcore_count = sum(1 for r in repos if r == 'kbhalerao/labcore'); print(f'kbhalerao/labcore: {labcore_count}/{len(hits)}'); other_repos = [r for r in unique if r != 'kbhalerao/labcore']; print(f'Other repos: {other_repos}')"
```

**CURRENT BEHAVIOR**: Returns results from multiple repos
**EXPECTED BEHAVIOR**: Returns ONLY results where `repo_id = "kbhalerao/labcore"` AND `type = "code_chunk"`

**Success Criteria**:
- `Unique repos: 1`
- `kbhalerao/labcore: 20/20` (or however many results)
- `Other repos: []` (empty list)

---

## Test 5: End-to-End API Test

### Test through the Python API
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "Django Channels background worker with job counter decorator", "stream": false}' \
  -s | python3 -c "import sys, json; data=json.load(sys.stdin); print('Response length:', len(data['answer']), 'chars'); print('First 200 chars:'); print(data['answer'][:200])"
```

### Check server logs for FTS request
```bash
tail -100 /tmp/api-server.log | grep -A 30 "FTS Request:" | head -40
```

### Verify results in logs
```bash
tail -200 /tmp/api-server.log | grep '"repo_id"' | grep -o '"repo_id": "[^"]*"' | sort | uniq -c
```

**Expected**: Should see ONLY `"repo_id": "kbhalerao/labcore"` (or `kbhalerao/labcore` + `test/code-smriti` test copy)

---

## Test 6: Verify Document Contents

### Check if expected files exist in index
```bash
curl -X POST http://$CB_HOST:8094/api/index/code_vector_index/query \
  -u $CB_USER:$CB_PASS \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "conjuncts": [
        {"term": "orders/consumers.py", "field": "file_path"},
        {"term": "kbhalerao/labcore", "field": "repo_id"}
      ]
    },
    "size": 10,
    "fields": ["repo_id", "file_path", "chunk_type", "code_text"]
  }' | python3 -c "import sys, json; data=json.load(sys.stdin); print('Found', len(data.get('hits', [])), 'chunks from orders/consumers.py'); [print(f\"  - {h['fields'].get('chunk_type', [''])[0]}: {h['fields'].get('code_text', [''])[0][:80]}...\") for h in data.get('hits', [])]"
```

**Expected**: Should find multiple chunks from `orders/consumers.py` in `kbhalerao/labcore`

---

## Test 7: Alternative Query Syntaxes to Try

### Option A: Single filter (not conjuncts)
```json
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 20,
    "filter": {
      "term": "kbhalerao/labcore",
      "field": "repo_id"
    }
  }]
}
```

### Option B: Match with analyzer parameter
```json
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 20,
    "filter": {
      "conjuncts": [
        {
          "match": "kbhalerao/labcore",
          "field": "repo_id",
          "analyzer": "keyword_analyzer"
        }
      ]
    }
  }]
}
```

### Option C: Phrase query
```json
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 20,
    "filter": {
      "phrase": "kbhalerao/labcore",
      "field": "repo_id"
    }
  }]
}
```

### Option D: Match with fuzziness 0
```json
{
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 20,
    "filter": {
      "match": "kbhalerao/labcore",
      "field": "repo_id",
      "fuzziness": 0
    }
  }]
}
```

---

## Debugging Commands

### Check index document count
```bash
curl -s -u $CB_USER:$CB_PASS \
  http://$CB_HOST:8094/api/index/code_vector_index/count
```

### Check index health
```bash
curl -s -u $CB_USER:$CB_PASS \
  http://$CB_HOST:8091/pools/default/buckets/code_kosha/stats | \
  python3 -c "import sys, json; data=json.load(sys.stdin); print('Item count:', data['op']['samples']['curr_items'][-1])"
```

### Get sample document
```bash
curl -s -u $CB_USER:$CB_PASS \
  http://$CB_HOST:8093/query/service \
  -d 'statement=SELECT META().id, type, repo_id, file_path FROM `code_kosha` WHERE type="code_chunk" AND repo_id="kbhalerao/labcore" LIMIT 1' | \
  python3 -m json.tool
```

### Check field analyzer
```bash
curl -s -u $CB_USER:$CB_PASS \
  "http://$CB_HOST:8094/api/index/code_vector_index/analyzeDoc" \
  -d '{"doc": {"type": "code_chunk", "repo_id": "kbhalerao/labcore", "file_path": "test.py"}}' | \
  python3 -m json.tool
```

---

## Success Criteria Summary

✅ **Test 2 passes**: term query without kNN returns only matching repo
✅ **Test 3 passes**: kNN without filter returns results from multiple repos
✅ **Test 4 passes**: kNN WITH filter returns ONLY from specified repo
✅ **Test 5 shows**: API logs confirm all results from correct repo
✅ **Performance**: Queries complete in <1 second

---

## Common Pitfalls

1. **Case sensitivity**: keyword_analyzer has `to_lower`, so terms must be lowercase
2. **Filter placement**: Filter MUST be inside kNN object, not at top level
3. **Query type**: `match` tokenizes, `term` doesn't - use `term` for exact match
4. **Analyzer mismatch**: Query analyzer must match index analyzer
5. **Field type**: Ensure field is indexed as text with keyword analyzer, not just keyword type

---

## Files to Check

- **FTS Index Config**: Via Couchbase UI at http://localhost:8091 → Search → code_vector_index
- **API Code**: `/Users/kaustubh/Documents/code/code-smriti/4-consume/api-server/app/chat/manual_rag_agent.py`
- **Server Logs**: `/tmp/api-server.log`
- **Test Questions**: `/Users/kaustubh/Documents/code/code-smriti/4-consume/api-server/tests/search_eval_questions.json`
