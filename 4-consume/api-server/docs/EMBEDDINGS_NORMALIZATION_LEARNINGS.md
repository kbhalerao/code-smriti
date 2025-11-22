# Embeddings & Normalization: Critical Learnings

**Date**: 2025-11-22
**Context**: Debugging vector search failure in code-smriti RAG system
**Root Cause**: Unnormalized embeddings breaking similarity scoring

## TL;DR

**Problem**: Vector search returned uniform scores (~264) regardless of semantic similarity.

**Root Cause**: Embeddings stored with magnitude ~16-20 instead of unit vectors (norm ≈ 1.0). FTS uses dot product, which was dominated by magnitude rather than direction.

**Solution**: Normalize all embeddings to unit vectors (norm = 1.0). Can be done in-place without re-ingestion.

**Impact**: Self-retrieval test went from FAIL (source doc not in top 10) to PASS (source doc ranked #1 with score 1.0).

---

## Past Experiments (What Didn't Work)

Before discovering the normalization issue, we tried several approaches that addressed symptoms but not the root cause:

### 1. Oversampling Strategy (Gemini's Suggestion)
**Problem**: Expected files not in top results
**Hypothesis**: FTS pre-filtering broken, need to retrieve more candidates
**Solution**: Retrieve 10x more results from FTS, filter in Python
**Implementation**:
```python
# Retrieve 10x more than needed
oversample_factor = 10
code_limit = max(limit * oversample_factor, 50)

# Filter in Python after retrieval
code_chunks = filter_by_type_and_criteria(fts_results)
return code_chunks[:limit]
```
**Result**: ❌ Still 0% match rate
**Why it failed**: Treated symptom (wrong ranking) not disease (broken scoring)

### 2. Data Cleanup
**Problem**: Empty files and tiny stubs polluting results
**Actions taken**:
- Modified ingestion to skip files < 100 chars
- Deleted existing small files from database (~1,548 files)
- Added `min_file_length` and `max_file_length` query parameters

**Implementation**:
```python
# Ingestion filter
if len(content.strip()) < 100:
    logger.debug(f"Skipping empty/small file: {file_path}")
    return []

# Query filter
where_clauses.append("metadata.file_size >= $min_file_length")
where_clauses.append("metadata.file_size <= $max_file_length")
```

**Result**: ⚠️ Improved data quality but didn't fix ranking
**Issue discovered**: Default `max_file_length=10000` filtered out some expected files (e.g., 11KB file)

### 3. FTS Index Rebuild
**Problem**: Suspected stale index data
**Action**: Deleted and recreated FTS index
**Result**: ❌ No change in behavior
**Learning**: Index was fine, embeddings were the problem

### 4. Hybrid Scoring Investigation
**Problem**: BM25 text scores overwhelming vector scores
**Observation**: Text search scores: 10-50, Vector scores: ~264
**Hypothesis**: Need to rebalance scoring weights
**Result**: ❌ Both scores were wrong because vectors weren't normalized

### 5. Query Rewriting Idea
**Observation**: Natural language queries don't match code patterns
**Example**: "how is job_counter implemented" vs `def job_counter(...)`
**Idea**: Rewrite natural language to code-like queries
**Status**: Deferred - need working vector search first
**Future**: May still be valuable for bridging semantic gap

### Progression Summary
1. **Week 1**: Built RAG system, got 0% match rate
2. **Week 2**: Added oversampling (no improvement)
3. **Week 3**: Cleaned data, added filtering (better data, still 0%)
4. **Week 4**: Self-retrieval test → discovered uniform scores
5. **Week 4**: Analyzed embeddings → **found normalization issue**
6. **Week 4**: In-place normalization → **100% self-retrieval success**

### Key Lesson from Failed Experiments
All previous attempts addressed **symptoms** (wrong results, bad ranking) rather than the **root cause** (unnormalized embeddings breaking dot product similarity). The self-retrieval test was critical in isolating the fundamental issue.

---

## The Problem

### Symptoms
- Vector search returning documents with nearly identical scores (~264)
- Self-retrieval test failing: document couldn't find itself using its own embedding
- Expected files not appearing in top results despite being semantically relevant

### Investigation Process

1. **Self-retrieval test**: Used a document's own embedding to search
   - Expected: Document ranks #1 with perfect score
   - Actual: Document not in top 10, all scores ~264

2. **Embedding analysis**: Checked stored embedding properties
   ```python
   embedding = np.array(stored_embedding)
   norm = np.linalg.norm(embedding)
   # Expected: norm ≈ 1.0 (unit normalized)
   # Actual: norm ≈ 16-18
   ```

3. **Similarity computation**: Manually computed cosine similarity
   - Self-similarity: 1.0 (perfect)
   - Cross-similarity with unrelated file: 0.7343
   - Embeddings were semantically correct!

4. **FTS scoring**: All documents got similar scores
   - Dot product = embedding1 · embedding2
   - For unnormalized: ~18 × 18 ≈ 324
   - Magnitude dominates, direction doesn't matter

---

## Root Cause: Missing normalize_embeddings=True

### The Embedding Model
- **Model**: `nomic-ai/nomic-embed-text-v1.5`
- **Library**: `sentence-transformers`
- **Dimensions**: 768

### Default Behavior
By default, `model.encode()` returns **unnormalized** embeddings:
```python
# WITHOUT normalization (WRONG)
embedding = model.encode(text)
# norm ≈ 16-20, varies by content
```

### Correct Approach
Must explicitly request normalization:
```python
# WITH normalization (CORRECT)
embedding = model.encode(text, normalize_embeddings=True)
# norm ≈ 1.0 (unit vector)
```

### Why Normalization Matters

**Dot Product (what FTS uses)**:
```
score = Σ(emb1[i] × emb2[i])
```

**For Unnormalized Vectors**:
- Dot product ≈ magnitude1 × magnitude2 × cosine(angle)
- Example: 18 × 18 × 0.95 ≈ 308
- Small angle variations (0.95 vs 0.90) barely change score

**For Normalized Vectors (unit vectors)**:
- Dot product = cosine similarity
- Example: 1.0 × 1.0 × 0.95 = 0.95
- Small variations create clear score differences

---

## The Fix: Two Approaches

### Approach 1: Full Re-Ingestion (Perfect Consistency)

**Update ingestion code** (`lib/ingestion-worker/embeddings/local_generator.py`):

1. Single embedding generation (line ~69):
```python
embedding = self.model.encode(
    text_with_prefix,
    convert_to_tensor=False,
    show_progress_bar=False,
    normalize_embeddings=True  # ADD THIS
)
```

2. Batch embedding generation (line ~174):
```python
batch_embeddings = self.model.encode(
    prefixed_batch,
    convert_to_tensor=False,
    show_progress_bar=False,
    batch_size=batch_size,
    normalize_embeddings=True  # ADD THIS
)
```

**Then**: Re-ingest all repositories

**Pros**: Perfect consistency, fresh embeddings
**Cons**: Time-consuming (hours for large codebases)

### Approach 2: In-Place Normalization (Fast Fix) ✅ USED

**Hypothesis**: If embeddings only differ in magnitude (not direction), normalizing in-place should work.

**Test Result**: In-place normalization produces identical ranking to fresh normalized embeddings!

**Implementation**:
```python
# For each document
embedding = np.array(doc['embedding'])
norm = np.linalg.norm(embedding)

if norm > 0 and abs(norm - 1.0) > 0.01:
    normalized = embedding / norm
    # Update in database using subdocument operations
    collection.mutate_in(
        doc_id,
        [subdocument.upsert("embedding", normalized.tolist())]
    )
```

**Stats for our dataset**:
- Total documents: 79,631
- Processing time: ~2 minutes
- Updates/second: ~660

**Pros**: Fast, no re-ingestion needed
**Cons**: Slight variation from fresh embeddings (0.96 similarity vs 1.0)

**Verification**:
```python
# After normalization
embedding = np.array(doc['embedding'])
norm = np.linalg.norm(embedding)
assert abs(norm - 1.0) < 0.01  # Should be ~1.0
```

---

## Key Insights

### 1. In-Place Normalization Works for Model Swaps

**Discovery**: We can swap embedding models without full re-ingestion!

**Process**:
1. Write a script to generate new embeddings for all documents
2. Normalize the new embeddings
3. Update documents in-place using subdocument operations
4. Rebuild FTS index

**Use case**: Upgrade from `nomic-embed-text-v1.5` to a newer model without days of re-ingestion.

### 2. Always Normalize Consistently

**Rule**: If you normalize at ingestion, you MUST normalize at query time (and vice versa).

**Our setup**:
- Ingestion: NOW uses `normalize_embeddings=True`
- Query: NOW uses `normalize_embeddings=True`
- Storage: Normalized vectors (norm ≈ 1.0)
- FTS: Dot product = cosine similarity

### 3. Couchbase FTS Uses Dot Product

**Important**: Couchbase FTS with `similarity: dot_product` computes:
```
score = Σ(query[i] × doc[i])
```

**For normalized vectors**:
- This is exactly cosine similarity
- Scores range from -1.0 to 1.0 (typically 0.0 to 1.0 for similar items)

**For unnormalized vectors**:
- This is meaningless (dominated by magnitude)
- Scores can be hundreds or thousands

### 4. Subdocument Operations Are Efficient

**Couchbase subdocument API** allows updating a single field:
```python
from couchbase import subdocument

collection.mutate_in(
    doc_id,
    [subdocument.upsert("embedding", new_embedding)]
)
```

**Benefits**:
- Faster than get-modify-replace
- Lower network bandwidth (only send the field)
- Atomic operation

**Critical**: Must use `subdocument.upsert()` not `collection.upsert()`

---

## Testing Strategy

### 1. Self-Retrieval Test (Identity Test)

**Purpose**: Verify search pipeline at the most basic level.

**Method**:
1. Select a known document
2. Use its own embedding as the query
3. Search FTS
4. Verify the document ranks #1 with score ≈ 1.0

**Why it matters**: If a document can't find itself, the pipeline is broken.

**Code**:
```python
# Get document with embedding
n1ql = "SELECT META().id, embedding FROM `bucket` WHERE ... LIMIT 1"
doc = db.query(n1ql).first()

# Search with its own embedding
fts_request = {
    "knn": [{
        "field": "embedding",
        "vector": doc['embedding'],
        "k": 20
    }]
}

# Verify doc['id'] is rank #1
```

### 2. In-Place vs Fresh Comparison Test

**Purpose**: Verify in-place normalization produces correct results.

**Method**:
1. Take stored unnormalized embedding
2. Normalize it: `normalized = embedding / np.linalg.norm(embedding)`
3. Generate fresh normalized embedding: `model.encode(text, normalize_embeddings=True)`
4. Compare rankings from both

**Results**:
- Both rank source document #1
- Scores: 1.0000 vs 1.0000
- Slight variation in other rankings (similarity 0.96)
- **Conclusion**: In-place normalization works!

### 3. Norm Verification

**Purpose**: Ensure all embeddings are unit vectors.

**Method**:
```python
for doc in random_sample(db, n=100):
    embedding = np.array(doc['embedding'])
    norm = np.linalg.norm(embedding)
    assert abs(norm - 1.0) < 0.01, f"Doc {doc['id']} has norm {norm}"
```

---

## Debugging Checklist

When vector search scores look wrong:

- [ ] Check embedding norms: `np.linalg.norm(embedding)` should be ≈ 1.0
- [ ] Verify ingestion uses `normalize_embeddings=True`
- [ ] Verify query generation uses `normalize_embeddings=True`
- [ ] Run self-retrieval test (document should find itself)
- [ ] Check FTS similarity metric matches normalization strategy
- [ ] Verify FTS index is up to date (rebuild if needed)
- [ ] Check model consistency (same model for ingestion and query)
- [ ] Verify embedding dimensions match FTS index definition

---

## Code Locations

### Ingestion
**File**: `/lib/ingestion-worker/embeddings/local_generator.py`
- Line ~69: Single embedding generation
- Line ~174: Batch embedding generation

### Query
**File**: `/4-consume/api-server/app/chat/manual_rag_agent.py`
- Line ~143: Query embedding generation

### Normalization Script
**File**: `/4-consume/api-server/normalize_embeddings_inplace.py`
- Normalizes all stored embeddings
- Uses subdocument operations
- Batch processing with progress logging

### Tests
**File**: `/4-consume/api-server/test_self_retrieval.py`
- Self-retrieval test (identity test)

**File**: `/4-consume/api-server/test_inplace_normalization.py`
- Compares in-place vs fresh normalization

**File**: `/4-consume/api-server/test_embedding_normalization.py`
- Comprehensive normalization analysis

---

## Performance Notes

### In-Place Normalization Performance
- **Dataset**: 79,631 documents
- **Duration**: ~2 minutes
- **Throughput**: ~660 updates/second
- **Batch size**: 1,000 documents per batch

### FTS Index Rebuild
- **Duration**: ~30 seconds for 79,631 documents
- **Triggered by**: Delete and recreate index
- **Auto-rebuild**: Yes, happens automatically

---

## Future Work

### 1. Embedding Model Upgrades
With in-place normalization, we can now:
1. Generate embeddings with new model
2. Update documents in-place
3. Rebuild FTS index
4. Test without full re-ingestion

### 2. Hybrid Scoring Improvements
Now that vector search works, investigate:
- Optimal weighting of BM25 vs vector scores
- Query rewriting for code vs natural language
- Filtering strategies (file type, repo, etc.)

### 3. Automated Validation
Add to CI/CD:
- Self-retrieval test (must pass)
- Norm verification (all embeddings ≈ 1.0)
- Sample query regression tests

---

## References

- **Model**: [nomic-ai/nomic-embed-text-v1.5](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5)
- **Library**: [sentence-transformers](https://www.sbert.net/)
- **Couchbase FTS**: [Vector Search Documentation](https://docs.couchbase.com/server/current/fts/fts-vector-search.html)
- **Normalization**: [Why normalize embeddings?](https://github.com/UKPLab/sentence-transformers/issues/1218)

---

## Conclusion

**The lesson**: Always check your assumptions. We assumed embeddings were normalized because that's common practice, but the library doesn't do it by default.

**The win**: Found a fast fix (in-place normalization) that avoided days of re-ingestion.

**The insight**: This technique generalizes to any batch vector update - model swaps, dimension reduction, even switching similarity metrics.

**The meta moment**: This document will now be indexed in code-smriti and could help future developers debug similar issues!
