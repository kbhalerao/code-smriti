# FTS Index vs Document Schema Comparison

## Summary of Findings

**🚨 CRITICAL ISSUE**: The `content` field is NOT indexed in FTS for any document type!
This means:
- No full-text search capability on code/documentation/commits
- Only vector similarity search is possible
- No hybrid (text + vector) search

## Detailed Comparison

### CODE_CHUNK

**Actual Document Fields:**
- chunk_id (str)
- chunk_type (str) ✅ indexed
- **content (str)** ❌ **NOT indexed - CRITICAL**
- created_at (str)
- embedding (768 dims) ✅ indexed
- **file_path (str)** ❌ NOT indexed
- language (str) ✅ indexed
- metadata (dict)
- repo_id (str) ✅ indexed
- type (str) ✅ indexed

**Missing from FTS:**
- ❌ **content** - The actual code (CRITICAL for text search)
- ❌ **file_path** - Useful for filtering by file patterns
- ❌ chunk_id - For deduplication/tracking
- ❌ created_at - For temporal filtering
- ❌ metadata - Contains author, commit_date, commit_hash

---

### DOCUMENT

**Actual Document Fields:**
- chunk_id (str)
- **content (str)** ❌ **NOT indexed - CRITICAL**
- created_at (str)
- doc_type (str) ✅ indexed
- embedding (768 dims) ✅ indexed
- **file_path (str)** ❌ NOT indexed
- metadata (dict)
- repo_id (str) ✅ indexed
- type (str) ✅ indexed

**Missing from FTS:**
- ❌ **content** - The actual documentation text (CRITICAL for text search)
- ❌ **file_path** - For filtering README, docs, etc.
- ❌ chunk_id - For deduplication/tracking
- ❌ created_at - For temporal filtering
- ❌ metadata - Contains author, commit_date, commit_hash

---

### COMMIT

**Actual Document Fields:**
- **author (str)** ❌ NOT indexed
- chunk_id (str)
- **commit_date (str)** ❌ NOT indexed
- **commit_hash (str)** ❌ NOT indexed
- **content (str)** ❌ **NOT indexed - CRITICAL**
- created_at (str)
- embedding (768 dims) ✅ indexed
- files_changed (list)
- repo_id (str) ✅ indexed
- type (str) ✅ indexed

**Missing from FTS:**
- ❌ **content** - The commit message (CRITICAL for text search)
- ❌ **author** - For filtering by developer
- ❌ **commit_date** - For temporal queries
- ❌ **commit_hash** - For exact commit lookups
- ❌ chunk_id - For deduplication/tracking
- ❌ created_at - For temporal filtering
- ❌ files_changed - For filtering by file

---

## Recommendations

### High Priority (Must Add)

1. **content** (all types) - Enable full-text search + hybrid search
   - Analyzer: `standard` (default)
   - Store: true (for highlighting)
   - Index: true

2. **file_path** (code_chunk, document) - Essential for file-based filtering
   - Analyzer: `keyword` (exact match + prefix search)
   - Index: true

### Medium Priority (Should Add)

3. **author** (commit) - Filter commits by developer
   - Analyzer: `keyword`
   - Index: true

4. **commit_date** (commit) - Temporal queries on commits
   - Type: `datetime`
   - Index: true

5. **commit_hash** (commit) - Exact commit lookups
   - Analyzer: `keyword`
   - Index: true

### Low Priority (Nice to Have)

6. **chunk_id** (all types) - Deduplication support
   - Analyzer: `keyword`
   - Index: true

7. **created_at** (all types) - Temporal filtering
   - Type: `datetime`
   - Index: true

8. **files_changed** (commit) - Filter by affected files
   - Analyzer: `keyword` (array)
   - Index: true

---

## Impact Analysis

**Current State:**
- Vector search: ✅ Works
- Text search: ❌ Impossible (no content indexed)
- Hybrid search: ❌ Impossible
- File filtering: ❌ Limited (only via N1QL post-filter)

**After Adding Recommended Fields:**
- Vector search: ✅ Works
- Text search: ✅ Enabled
- Hybrid search: ✅ Enabled (combine text + vector scores)
- File filtering: ✅ Fast FTS-level filtering
- Commit filtering: ✅ By author, date, hash

---

## Next Steps

1. Update FTS index definition to add missing fields
2. Rebuild index (or wait for incremental indexing)
3. Update `search_code_tool()` to support hybrid text+vector search
4. Add file_path filtering support to tool
