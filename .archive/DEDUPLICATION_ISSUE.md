# Deduplication Issue - 2025-11-19

## Problem Summary

**80% of chunks are being lost during ingestion** due to duplicate chunk IDs causing overwrites.

### Evidence:
- Logs show: "55,549 succeeded, 1 failed" for upserts
- Database contains: Only 11,128 unique documents
- Deduplication rate: 44,421 chunks lost (80%)

### Root Cause Investigation

**Chunk ID Generation:**
```python
# CodeChunk
chunk_key = f"{repo_id}:{file_path}:{commit_hash}:{start_line}"
chunk_id = hashlib.sha256(chunk_key.encode()).hexdigest()

# DocumentChunk
chunk_key = f"{repo_id}:{file_path}:{commit_hash}"
chunk_id = hashlib.sha256(chunk_key.encode()).hexdigest()
```

**Observations:**
1. ✅ File paths are unique and correctly stored (`repos/kbhalerao_labcore/...`)
2. ✅ Start lines are present and vary (6, 7, 8, etc. - though 201 docs have NULL)
3. ❌ **commit_hash is NULL/missing** in stored metadata
4. ⚠️ `test/code-smriti` was the wrong test repo (it's THIS codebase, creating recursion)
5. ⚠️ Nested `repos/kbhalerao_labcore/` likely has no git history detectable by parent repo

### Hypothesis

The nested `repos/kbhalerao_labcore/` directory is inside the cloned `test_code-smriti` repo, but it's a separate git repo (submodule or bare directory). When `get_git_metadata()` tries to find commits for these files:

```python
commits = list(repo.iter_commits(paths=file_path, max_count=1))
# Returns [] for nested repo files
# Therefore: commit_hash = "no_commit" for all 55K chunks
```

But this STILL shouldn't cause 80% deduplication since file_paths are unique...

### Potential Issues:

1. **Bug in chunk ID generation** - Maybe file_path isn't being included properly?
2. **Git metadata extraction** - Nested repo files have no traceable history
3. **Repository structure** - `test/code-smriti` contains nested `repos/` folder

### Files with Missing start_line (201 docs):
- Document chunks (`.md`, `.txt`, `.yaml`, `.rst`) - expected
- Examples: `QUICKSTART.md`, `docs/MCP-USAGE.md`, etc.

### Next Steps to Debug:

1. **Add logging to chunk ID generation**:
   ```python
   logger.debug(f"Generating chunk_id: {chunk_key}")
   ```

2. **Check for actual duplicate chunk_ids**:
   ```sql
   SELECT chunk_id, COUNT(*) FROM chunks GROUP BY chunk_id HAVING COUNT(*) > 1
   ```
   (Can't do this in Couchbase easily since chunk_id is the document key)

3. **Test with a proper external repo** (not `test/code-smriti`):
   - Use a real small public repo
   - Or just run `kbhalerao/labcore` directly

4. **Verify git metadata extraction**:
   - Add debug logging to `get_git_metadata()`
   - Check what commit_hash is actually being returned

5. **Add uniqueness to chunk IDs**:
   - Include a sequence number or hash of content
   - Change to: `{repo}:{file}:{commit}:{line}:{content_hash[:8]}`

## Temporary Workaround

For now, the 11,128 chunks we DO have are:
- Properly stored with deterministic IDs
- Have correct file paths and metadata
- Should work for basic functionality

But we're missing 80% of the codebase, which is unacceptable for production.

## Test Plan for Fix

1. Clean database
2. Test with simple external repo (e.g., `octocat/Hello-World`)
3. Verify all chunks stored (expected count matches actual count)
4. Then test with `kbhalerao/labcore`
5. Verify commit_hash is properly extracted
6. Test incremental updates

## Configuration Notes

Current `.env`:
- `GITHUB_REPOS=kbhalerao/labcore`
- `EMBEDDING_BACKEND=local`
- `ENABLE_INCREMENTAL_UPDATES=false` (disabled for first run)

Repository paths:
- Project root: `/Users/kaustubh/Documents/code/code-smriti/`
- Repos folder: `/Users/kaustubh/Documents/code/code-smriti/repos/`
- Contains: `kbhalerao_labcore/` and `test_code-smriti/` (DON'T USE)
