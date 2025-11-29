# RAG API Migration Guide: V3 to V4

**Date**: 2025-11-29
**Status**: Migration Required

## Overview

This document details the required changes to the RAG API (`services/api-server`) to support V4 document schema while maintaining backward compatibility during migration.

## Files Requiring Changes

| File | Priority | Changes |
|------|----------|---------|
| `app/chat/routes.py` | HIGH | Update queries, doc types, field access |
| `app/chat/pydantic_rag_agent.py` | HIGH | Update search result parsing |
| `app/database/couchbase_client.py` | MEDIUM | Add V4-aware queries |

## Detailed Changes

### 1. `app/chat/routes.py`

#### 1.1 SearchRequest Model (line 103-104)

**Before:**
```python
doc_type: str = Field(
    default="code_chunk",
    description="Document type: code_chunk, document, or commit"
)
```

**After:**
```python
doc_type: str = Field(
    default="file_index",
    description="Document type: file_index, symbol_index, module_summary, repo_summary (V4) or code_chunk (legacy)"
)
```

#### 1.2 N1QL Query Field Access (lines 409-414)

**Before:**
```python
n1ql = f"""
    SELECT META().id, repo_id, file_path, content, `language`,
           start_line, end_line, type
    FROM `{tenant_id}`
    WHERE {where_clause}
"""
```

**After:**
```python
n1ql = f"""
    SELECT META().id, repo_id, file_path, content, type,
           CASE WHEN type IN ['file_index', 'symbol_index']
                THEN metadata.language
                ELSE `language` END as language,
           CASE WHEN type IN ['file_index', 'symbol_index']
                THEN metadata.start_line
                ELSE start_line END as start_line,
           CASE WHEN type IN ['file_index', 'symbol_index']
                THEN metadata.end_line
                ELSE end_line END as end_line
    FROM `{tenant_id}`
    WHERE {where_clause}
"""
```

**Alternative (simpler, V4-only after migration):**
```python
n1ql = f"""
    SELECT META().id, repo_id, file_path, content, type,
           metadata.language as language,
           metadata.start_line as start_line,
           metadata.end_line as end_line
    FROM `{tenant_id}`
    WHERE {where_clause}
"""
```

#### 1.3 Repos Listing Query (lines 502-508)

**Before:**
```python
n1ql = f"""
    SELECT repo_id, COUNT(*) as doc_count
    FROM `{tenant}`
    WHERE repo_id IS NOT MISSING AND type = 'code_chunk'
    GROUP BY repo_id
    ORDER BY doc_count DESC
"""
```

**After:**
```python
n1ql = f"""
    SELECT repo_id, COUNT(*) as doc_count
    FROM `{tenant}`
    WHERE repo_id IS NOT MISSING
      AND type IN ['file_index', 'symbol_index', 'module_summary', 'repo_summary']
    GROUP BY repo_id
    ORDER BY doc_count DESC
"""
```

### 2. `app/chat/pydantic_rag_agent.py`

#### 2.1 Document Field Access (lines 258-266)

**Before:**
```python
code_chunks.append(CodeChunk(
    content=doc.get('content', doc.get('code_text', '')),
    repo_id=doc.get('repo_id', ''),
    file_path=doc.get('file_path', ''),
    language=doc.get('language', ''),
    score=hit.get('score', 0.0),
    start_line=doc.get('start_line'),
    end_line=doc.get('end_line')
))
```

**After:**
```python
# Handle both V3 (root level) and V4 (metadata nested)
metadata = doc.get('metadata', {})
code_chunks.append(CodeChunk(
    content=doc.get('content', ''),
    repo_id=doc.get('repo_id', ''),
    file_path=doc.get('file_path', ''),
    language=metadata.get('language', doc.get('language', '')),
    score=hit.get('score', 0.0),
    start_line=metadata.get('start_line', doc.get('start_line')),
    end_line=metadata.get('end_line', doc.get('end_line'))
))
```

### 3. FTS Index Considerations

The existing `code_vector_index` should work with V4 documents since:
- `embedding` field is at root level (same as V3)
- `content` field is at root level (same as V3)
- `repo_id` field is at root level (same as V3)
- `file_path` field is at root level (same as V3)

**New fields to consider indexing:**
- `type` (for filtering by document type)
- `symbol_name` (for symbol search)
- `metadata.language` (for language filtering)

## Testing Strategy

### 1. Local Testing (No Docker Restart)

Run the api-server locally against the live Couchbase:

```bash
cd services/api-server
source .venv/bin/activate

# Set environment to point to live Couchbase
export COUCHBASE_HOST=localhost  # or macstudio.local
export COUCHBASE_USERNAME=Administrator
export COUCHBASE_PASSWORD=<password>
export COUCHBASE_BUCKET=code_kosha

# Run locally on a different port
uvicorn app:app --host 0.0.0.0 --port 8001
```

### 2. Test Queries

```bash
# Test V4 document search
curl -X POST http://localhost:8001/api/rag/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "authentication", "doc_type": "file_index", "limit": 5}'

# Test repos listing
curl -X GET http://localhost:8001/api/rag/repos \
  -H "Authorization: Bearer $TOKEN"

# Test RAG chat
curl -X POST http://localhost:8001/api/rag/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "How does the job_counter decorator work?", "stream": false}'
```

### 3. Validation Queries (Couchbase Console)

```sql
-- Check V4 document counts by type
SELECT type, COUNT(*) as count
FROM `code_kosha`
WHERE version.schema_version = 'v4.0'
GROUP BY type;

-- Verify file_index has metadata.language
SELECT file_path, metadata.language
FROM `code_kosha`
WHERE type = 'file_index'
LIMIT 5;

-- Check symbol_index structure
SELECT symbol_name, file_path, metadata.start_line, metadata.end_line
FROM `code_kosha`
WHERE type = 'symbol_index'
LIMIT 5;
```

## Rollback Plan

If issues arise after deployment:

1. The code changes support both V3 and V4 field locations
2. V3 documents still exist in Couchbase until explicitly deleted
3. To revert to V3-only: change `doc_type` default back to `code_chunk`

## Deployment Steps

1. **Test locally** against live Couchbase (port 8001)
2. **Verify V4 queries** return expected results
3. **Update Docker image** with new code
4. **Rolling restart** of api-server container (after ingestion completes)

## Post-Migration Cleanup

After V4 ingestion completes and is verified:

```sql
-- Delete V3 documents (optional, after verification)
DELETE FROM `code_kosha`
WHERE type IN ['code_chunk', 'document', 'commit']
  AND (version.schema_version IS MISSING OR version.schema_version != 'v4.0');
```

## Appendix: V4 Document Type Summary

| Type | Description | Parent | Children |
|------|-------------|--------|----------|
| `repo_summary` | Repository overview | None | module_summary |
| `module_summary` | Folder/directory summary | repo_summary or module_summary | file_index or module_summary |
| `file_index` | File summary with all symbols | module_summary | symbol_index (significant only) |
| `symbol_index` | Function/class summary (>= 5 lines) | file_index | None |
