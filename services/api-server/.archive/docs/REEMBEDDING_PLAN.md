# Re-Embedding Plan: Upgrade to nomic-ai

## Current Situation
- **Database**: 116,898 chunks
  - 113,751 chunks (97.3%) with `all-mpnet-base-v2` embeddings
  - 3,147 chunks (2.7%) missing embeddings
- **Problem**: Poor search quality because all-mpnet-base-v2 is general-purpose, not optimized for code

## Target Model
**`nomic-ai/nomic-embed-text-v1.5`**
- 768 dimensions (same as current, no FTS reconfig needed)
- Optimized for code + text retrieval
- Long context support (8192 tokens)
- Better semantic understanding for technical content

## Changes Made

### 1. Query Model (4-consume/api-server)
✅ Updated `app/chat/simple_agent.py`:
```python
model = SentenceTransformer('nomic-ai/nomic-embed-text-v1.5')
query_with_prefix = f"search_document: {query}"
```

### 2. Ingestion Model (lib/ingestion-worker)
✅ Updated `embeddings/local_generator.py`:
```python
model_name = "nomic-ai/nomic-embed-text-v1.5"
```

### 3. Re-Embedding Script
✅ Created `reembed_with_nomic.py`:
- Processes ALL 116K chunks in batches of 128
- Uses nomic-ai model with proper prefix
- Estimated time: ~30 minutes
- Progress logging every 10 batches

## FTS Index Status
✅ Index `code_vector_index`:
- Type: fulltext-index
- Source bucket: code_kosha
- Dimensions: 768 (compatible with nomic-ai)
- Similarity: dot_product
- **No reconfiguration needed**

## Execution Plan

### Step 1: Wait for Current Ingestion
Check database for repos still being ingested

### Step 2: Run Re-Embedding
```bash
cd /Users/kaustubh/Documents/code/code-smriti/4-consume/api-server
source venv/bin/activate
python3 reembed_with_nomic.py | tee /tmp/reembedding.log
```

### Step 3: Verify Search Quality
Test with known queries:
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "channels background worker job counter"}'
```

Expected: Should now find `@job_counter` decorator usage in labcore

### Step 4: Monitor Performance
- Check search result relevance
- Verify similarity scores improve
- Compare before/after search quality

## Expected Improvements
1. ✅ Better code semantic matching
2. ✅ All chunks will have embeddings (no more missing 2.7%)
3. ✅ `@job_counter` and similar code patterns now findable
4. ✅ Improved cross-language code search

## Rollback Plan (if needed)
If search quality degrades:
1. Keep backfill_embeddings.py (uses all-mpnet-base-v2)
2. Revert ingestion/query models to all-mpnet-base-v2
3. Re-run with old model

## Notes
- Both models use 768 dimensions → no index changes
- Prefix `"search_document:"` maintained for consistency
- Batch size 128 optimized for M2 Max GPU/MPS
- Normalize embeddings enabled (nomic recommendation)
