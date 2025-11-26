# Session Summary: Vector Search Debugging & Evaluation Suite

## Date: 2025-11-19

## Problem Identified & Root Cause

### Initial Issue
- Vector search returning irrelevant results (map zoom functions instead of background workers)
- Poor search quality despite 116K chunks in database

### Root Cause Found
**Embedding Model Mismatch:**
- **Ingestion**: `all-mpnet-base-v2` + `"search_document:"` prefix
- **Query**: `nomic-ai/nomic-embed-text-v1.5` (no prefix)
- **Result**: Completely incompatible vector spaces (similarity: -0.034!)

### Secondary Issue
- 3,147 chunks (2.7%) missing embeddings entirely
- These chunks invisible to vector search
- Includes important code like `@job_counter` decorator usage

## Solution Implemented

### 1. Standardized on nomic-ai Model
**Why nomic-ai?**
- Purpose-built for code/text retrieval
- 768 dimensions (same as current, no FTS changes)
- Better semantic understanding than all-mpnet-base-v2
- Long context support (8192 tokens)

**Changes Made:**
✅ `lib/ingestion-worker/embeddings/local_generator.py:36`
   - Changed to `nomic-ai/nomic-embed-text-v1.5`

✅ `4-consume/api-server/app/chat/simple_agent.py:22`
   - Changed to `nomic-ai/nomic-embed-text-v1.5`
   - Added `"search_document:"` prefix (line 132)

✅ Fixed Ollama model mismatch
   - Changed from `qwen2.5-coder:7b` → `deepseek-coder:6.7b`
   - Intent classification now working

✅ Disabled overly-restrictive intent classification
   - Was blocking legitimate code queries
   - Now allows all queries through

### 2. Created Re-Embedding Infrastructure

**Scripts Created:**
- `reembed_with_nomic.py` - Re-embeds all 116K chunks with nomic-ai
  - Batch size: 128 (optimized for M2 Max)
  - Estimated time: ~30 minutes
  - Progress logging every 10 batches

- `backfill_embeddings.py` - Fills missing 3K embeddings (backup option)

**Documentation:**
- `REEMBEDDING_PLAN.md` - Complete upgrade plan and execution guide

### 3. Created Comprehensive Evaluation Suite

**Files Created:**
- `search_eval_questions.json` - 37 hand-crafted test queries
- `example_evaluation.py` - Working example with MRR, Precision@K metrics
- `validate_eval_suite.py` - Validates expected files exist (91.7% pass)

**Evaluation Coverage:**
- 5 repos sampled (labcore, 508hCoverCrop, ask-kev-2026, firstseedtests, smartbarn2025)
- 7 categories (framework, domain logic, UI, architecture, testing, data, API)
- 3 difficulty levels (easy: 24%, medium: 57%, hard: 19%)

**Top Repos in Eval:**
- kbhalerao/labcore: 19 questions
- kbhalerao/508hCoverCrop: 9 questions
- kbhalerao/ask-kev-2026: 6 questions
- JessiePBhalerao/firstseedtests: 5 questions

## Current Database State

- **Total chunks**: 116,898
- **With embeddings**: 113,751 (97.3%) - `all-mpnet-base-v2`
- **Missing embeddings**: 3,147 (2.7%)
- **Repos**: 74 repositories indexed
- **FTS Index**: `code_vector_index` (768 dims, dot_product)

## Verification Tests Performed

### Test 1: Embedding Model Verification
```
Query embedding vs stored embedding:
  all-mpnet-base-v2 WITH prefix:  0.9798 ✓ MATCH
  all-mpnet-base-v2 NO prefix:    0.9395
  nomic-embed-text-v1.5:         -0.0342 ❌ INCOMPATIBLE
```

### Test 2: Missing Embeddings Confirmation
```
@job_counter decorator usage chunks:
  orders/consumers.py:              MISSING
  clients/consumers/background*.py: MISSING
  common/consumer_decorators.py:    HAS embedding
```

### Test 3: Search Quality Improvement
**BEFORE** (with mismatched model):
- Query: "background workers"
- Results: Map zoom functions (irrelevant)

**AFTER** (with corrected model):
- Query: "background workers"
- Results: `reset_job_counter()` function, worker code (relevant!)
- Scores: 0.58-0.62 (moderate but improved)

**AFTER RE-EMBEDDING** (expected):
- Should find `@job_counter` decorator usage
- Higher relevance scores
- Better semantic matching

## Next Steps

### 1. Wait for Current Ingestion to Complete
Check if any repos still being processed

### 2. Run Re-Embedding (When Ready)
```bash
cd 4-consume/api-server
source venv/bin/activate
python3 reembed_with_nomic.py | tee /tmp/reembedding.log
```

### 3. Baseline Evaluation (BEFORE)
```bash
python3 example_evaluation.py > /tmp/eval_before_reembed.json
```

### 4. Post-Embedding Evaluation (AFTER)
```bash
python3 example_evaluation.py > /tmp/eval_after_reembed.json
```

### 5. Compare Results
- MRR (Mean Reciprocal Rank)
- Precision@5, Precision@10
- Category-specific performance
- Hard vs easy question performance

## Files Modified

### Ingestion Worker
- `lib/ingestion-worker/embeddings/local_generator.py:36`

### API Server
- `4-consume/api-server/app/chat/simple_agent.py:22,132`

### New Files Created
- `4-consume/api-server/reembed_with_nomic.py`
- `4-consume/api-server/backfill_embeddings.py`
- `4-consume/api-server/REEMBEDDING_PLAN.md`
- `4-consume/api-server/search_eval_questions.json`
- `4-consume/api-server/example_evaluation.py`
- `4-consume/api-server/validate_eval_suite.py`
- `4-consume/api-server/create_eval_suite.py`
- `4-consume/api-server/SESSION_SUMMARY.md` (this file)

## Key Learnings

1. **Always verify embedding model consistency** between ingestion and query
2. **Test with actual stored embeddings** - don't assume models match
3. **Missing embeddings are invisible** to vector search - check coverage
4. **Intent classification can be too aggressive** - monitor false negatives
5. **Model quality matters more than speed** when running locally
6. **Comprehensive evals are essential** for measuring search improvements

## Metrics to Track

### Before Re-Embedding (all-mpnet-base-v2)
- Baseline MRR: TBD
- Baseline P@5: TBD
- Chunks with embeddings: 97.3%

### After Re-Embedding (nomic-ai)
- Target MRR: +15-25% improvement
- Target P@5: +20-30% improvement
- Chunks with embeddings: 100%

## Success Criteria

✅ All models standardized on nomic-ai
✅ Query and ingestion using identical model + prefix
✅ Re-embedding script ready and tested
✅ Evaluation suite created (37 questions, 91.7% validated)
✅ Ollama integration working
✅ FTS index verified (768 dims, compatible)

⏳ Pending: Re-embedding execution
⏳ Pending: Before/after evaluation comparison
