# RAG Response Quality Issues

**Date**: 2025-11-20
**Status**: 🚨 CRITICAL - 78.4% generic responses
**Evaluation**: 37-question test set

---

## Executive Summary

While the manual RAG agent **technically works** (100% success rate, no crashes, tools execute), the **response quality is poor**:

- ✅ **21.6% (8/37)** responses contain actual code from indexed repositories
- ❌ **78.4% (29/37)** responses are generic examples not from the codebase
- ❌ **75.7% (28/37)** questions didn't find expected files

**Impact**: System is not useful for actual code search - users get generic StackOverflow-style answers instead of their actual codebase.

---

## Quality Analysis Results

### Responses with Actual Repo Code (Good ✓)

**8 questions found real code:**
- Q2: requeue task decorator - found `kbhalerao/devanand`
- Q3: PDF generation - found `agkit.io-backend`, `evolvechiro`, `devanand`
- Q14: Redis integration - found `BackgroundRegressionConsumer` ✨ (Best example)
- Q19: async/sync wrapper - found `kbhalerao/`
- Q23: pytest fixtures - found `kbhalerao/`
- Q24: model permissions - found `devanand`
- Q27: raster data processing - found `kbhalerao/`
- Q33: insurance data population - found `kbhalerao/`, `evolvechiro`

**Example of good response (Q14)**:
```python
class BackgroundRegressionConsumer(SyncConsumer):
    def receive_message(self, message):
        redis_client = StrictRedis(host='localhost', port=6379, db=0)
        job_id = message['headers']['job_id']
        redis_client.hset(job_id, 'status', 'in_progress')
```
This is **actual code** from `clients/consumers/backgroundconsumers.py` in the `kbhalerao/labcore` repo!

### Generic Responses (Bad ✗)

**29 questions returned generic examples:**
- Q1: "Here is a JSON response..." (didn't execute tool!)
- Q4-Q13: Generic Django/Python examples
- Q15-Q22: Generic framework code
- Q25-Q26, Q28-Q32, Q34-Q37: Generic examples

**Example of bad response (Q5 - error handling)**:
```python
from celery import shared_task
from celery.exceptions import Ignore, Reject

@shared_task(bind=True)
def my_background_task(self):
    try:
        # Task logic here
    except Exception as e:
        logger.error(f"Task failed: {e}", exc_info=True)
```
This looks like it came from StackOverflow or documentation, **not from the indexed codebase**.

---

## Root Cause Analysis

### 1. Vector Search Failure (Primary Issue)

**Evidence:**
- 28/37 questions didn't find expected files
- Example: Q1 expected `orders/consumers.py` and `common/consumer_decorators.py` - found neither

**Hypothesis:**
1. **Embedding mismatch**: Query embeddings don't align with code embeddings
   - Using `search_document:` prefix for queries
   - Code was indexed with different/no prefix
   - Semantic gap between natural language queries and code

2. **Insufficient code chunks**: Not enough context indexed
   - Current limit: 10 chunks per query
   - May need more chunks or better ranking

3. **Index quality**: Vector index may not be optimized
   - 46,089 total documents but only ~29,193 code chunks
   - May need reindexing with better chunking strategy

### 2. System Prompt Removal Side Effect (Secondary)

**Trade-off we made:**
- ✅ Removed system prompt → tools work
- ❌ No guidance → LLM generates generic examples

**Without system prompt**, llama3.1 doesn't know to:
- Use search results instead of making up examples
- Cite specific files and repos
- Admit when search finds nothing relevant

### 3. Tool Execution Reliability (Minor)

**Mostly working:**
- 36/37 questions successfully executed tools
- Only Q1, Q12 returned raw JSON

**But execution doesn't guarantee quality** - tools run but results aren't used!

---

## Diagnostic Tests Needed

### Test 1: Manual Vector Search Validation
```bash
# Test if vector search finds expected code for Q1
query="Django Channels background worker decorator"
expected_files=["orders/consumers.py", "common/consumer_decorators.py"]

# Run direct FTS query and check results
curl -X POST http://localhost:8094/api/index/code_vector_index/query \
  -u Administrator:password123 \
  -H "Content-Type: application/json" \
  -d '{"query": {"match_none": {}}, "knn": [{"field": "embedding", "vector": [...], "k": 10}]}'
```

**Expected**: Should find `orders/consumers.py` with `@count_jobs_in_queue` decorator

**Actual**: Likely returns irrelevant code or low-similarity matches

### Test 2: Embedding Alignment Check
```python
# Compare query embedding vs code embedding similarity
query_embedding = embedding_model.encode("search_document: Django Channels background worker")
code_embedding = embedding_model.encode("@count_jobs_in_queue decorator in consumers.py")

cosine_similarity = dot(query_embedding, code_embedding) / (norm(query_embedding) * norm(code_embedding))
```

**Expected**: >0.7 similarity
**Hypothesis**: <0.5 similarity (semantic gap)

### Test 3: LLM Prompt Sensitivity
```python
# Test if adding instruction helps
prompt_a = "Django Channels background worker"  # No instruction
prompt_b = "Using ONLY the search results below, show Django Channels code:\n[search results]"

# Compare response quality
```

**Hypothesis**: LLM needs explicit instruction to use search results

---

## Improvement Recommendations

### Priority 1: Fix Vector Search (Critical)

**Option A: Improve query embeddings** (Quick win)
```python
# Current:
query_embedding = model.encode(f"search_document: {query}")

# Try:
query_embedding = model.encode(query)  # Remove prefix
# Or:
query_embedding = model.encode(f"code: {query}")  # Match indexing prefix
```

**Option B: Reindex with better chunking**
1. Use smaller chunks (current: 500 chars → try 300 chars)
2. Add overlap between chunks
3. Index more context (function + docstring + imports)

**Option C: Hybrid search** (Best long-term)
```python
# Combine vector search + keyword search
search_request = {
    "query": {"match": "Django Channels", "field": "code_text"},  # Keyword
    "knn": [{"field": "embedding", "vector": query_embedding, "k": 10}]  # Semantic
}
```

### Priority 2: Add Minimal System Prompt (High)

**Challenge**: System prompts broke tool calling before

**Proposed approach**: Very minimal, specific instruction
```python
SYSTEM_PROMPT = "Use the search_code tool to find relevant code. Only show code from the search results."
```

**Test carefully** to ensure tools still execute!

### Priority 3: Improve Result Formatting (Medium)

**Current**: Tool returns truncated code chunks (500 chars)
```python
code_chunks.append({
    "content": doc.get('code_text', '')[:500],  # TRUNCATED!
})
```

**Proposed**:
```python
code_chunks.append({
    "content": doc.get('code_text', '')[:1000],  # More context
    "file_path": doc.get('file_path', ''),
    "repo_id": doc.get('repo_id', ''),
    "score": hit.get('score', 0.0),
    "start_line": doc.get('start_line'),
    "end_line": doc.get('end_line')
})
```

Add explicit formatting in response:
```
File: {file_path}:{start_line}-{end_line}
Repo: {repo_id}
Score: {score}

{content}
```

### Priority 4: Add Response Quality Validation (Low)

```python
def validate_response_quality(query: str, response: str, search_results: List[Dict]) -> float:
    """Score response quality based on search result usage."""

    # Check if response mentions files from search results
    mentioned_files = sum(1 for result in search_results if result['file_path'] in response)
    file_score = mentioned_files / len(search_results)

    # Check if response has code blocks
    has_code = '```' in response

    # Check for generic patterns
    has_generic = any(pattern in response for pattern in [
        'here is an example',
        'you can use',
        'stackoverflowcom'
    ])

    quality_score = file_score * 0.5 + (0.3 if has_code else 0) - (0.3 if has_generic else 0)
    return max(0, min(1, quality_score))
```

---

## Expected Improvements

### After Priority 1 + 2 fixes:

| Metric | Current | Target | Stretch Goal |
|--------|---------|--------|--------------|
| Responses with actual code | 21.6% | **60%** | 80% |
| Found expected files | 24.3% | **50%** | 70% |
| Generic responses | 78.4% | **30%** | 15% |

### Success Criteria

A response is "good" if it:
1. ✅ Contains code from the actual indexed repositories
2. ✅ Cites specific files and line numbers
3. ✅ Matches expected files for the query
4. ❌ Does NOT have generic "Here's an example" code

---

## Testing Protocol

After implementing fixes:

```bash
# 1. Run diagnostic tests
python test_vector_search_quality.py

# 2. Run small eval (5 questions)
python run_rag_eval.py 5 0

# 3. Analyze quality
python analyze_response_quality.py eval_results_final_*.json

# 4. If quality >50%, run full eval
python run_rag_eval.py 5 0  # All 37 questions

# 5. Compare metrics
# Target: >60% responses with actual code
```

---

## Files for Analysis

- `eval_results_final_20251120_231316.json` - Original eval (21.6% quality)
- `analyze_response_quality.py` - Quality analysis script
- `run_rag_eval.py` - Evaluation harness

---

## Next Steps

1. ⏳ Run diagnostic tests to confirm root cause
2. ⏳ Implement Priority 1 fix (vector search improvement)
3. ⏳ Test with 5-question eval
4. ⏳ Implement Priority 2 fix (minimal system prompt)
5. ⏳ Run full 37-question eval
6. ⏳ Compare before/after quality metrics

---

## Conclusion

The manual RAG agent **infrastructure works** (tools execute, system stable), but the **AI/search quality is poor**. This is primarily a **vector search problem**, not a tool calling problem.

**Key insight**: We successfully bypassed the pydantic-ai tool execution bug, but revealed a deeper issue - the semantic search isn't finding relevant code.

**Recommendation**: Focus on vector search quality before scaling throughput. A fast system that returns generic code is not useful.
