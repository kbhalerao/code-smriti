# Code-Smriti Ingestion Pipeline

## Overview

The ingestion pipeline processes GitHub repositories and stores them in Couchbase for semantic search and code analysis.

## Repository List

**File:** `repos_to_ingest.txt`
- Contains 100 repositories sorted by most recent commit activity
- Format: `owner/repo-name` (one per line)
- Comments start with `#`
- Completed repos marked with `[DONE]`
- In-progress repos marked with `[IN PROGRESS]`

## Scripts

### 1. `final_ingestion.py` (Current Production)
Ingests the initial 3 core repositories:
- `kbhalerao/claude-idea` (smallest, ~25 chunks)
- `kbhalerao/code-smriti` (medium, ~45 chunks)
- `kbhalerao/labcore` (largest, ~55K chunks)

**Usage:**
```bash
source ingestion-worker/venv/bin/activate
python3 final_ingestion.py
```

**Status:** Currently running, processing labcore embeddings

### 2. `pipeline_ingestion.py` (Future Batch Processing)
Processes all repositories from `repos_to_ingest.txt` in order.

**Features:**
- Reads repos from file (skips `[DONE]` entries)
- Processes in order of commit recency
- Interactive confirmation before starting
- Asks to continue on failures
- Shows progress and final statistics

**Usage:**
```bash
source ingestion-worker/venv/bin/activate
python3 pipeline_ingestion.py
```

### 3. `test_full_ingestion.py` (Single Repo Testing)
Test script for validating single repository ingestion.

## Pipeline Workflow

### Current State (2025-11-19)
1. ✅ Database cleaned
2. ✅ `claude-idea` ingested (25 chunks)
3. ✅ `code-smriti` ingested (45 chunks)
4. ⏳ `labcore` in progress (55,512 chunks being embedded)

### Next Steps
Once `labcore` completes:
1. Verify all chunks stored correctly (~55,582 total)
2. Mark repos as `[DONE]` in `repos_to_ingest.txt`
3. Run `pipeline_ingestion.py` for next batch

## Key Features Implemented

### 1. Content-Hash Based Chunk IDs
- Format: `sha256(repo:file:commit:content_hash)`
- Guarantees uniqueness
- No more deduplication issues

### 2. Separate Commit Documents
- Commit metadata stored once per commit
- Referenced by multiple chunks
- Searchable commit messages
- Storage efficient

### 3. File-Level Incremental Updates
- When file changes: delete all old chunks → re-parse → store new
- Atomic file-level updates
- Promotes modularity

### 4. Recursion Prevention
- Repos stored in `/Users/kaustubh/Documents/codesmriti-repos/`
- Outside project directory
- Safe to ingest `code-smriti` itself

## Configuration

**Environment Variables (`.env`):**
```bash
REPOS_PATH=/Users/kaustubh/Documents/codesmriti-repos
GITHUB_TOKEN=ghp_...
EMBEDDING_BACKEND=local
COUCHBASE_HOST=localhost
COUCHBASE_USERNAME=Administrator
COUCHBASE_PASSWORD=password123
COUCHBASE_BUCKET=code_kosha
```

## Performance

**Observed Performance:**
- Small repos (~25 chunks): ~1-2 seconds
- Medium repos (~45 chunks): ~2-3 seconds
- Large repos (~55K chunks): ~10-15 minutes
  - Parsing: ~2 minutes
  - Embedding: ~8 minutes (434 batches @ 128 chunks/batch)
  - Storage: ~2 minutes

**Embedding Speed:**
- ~10 batches/minute
- ~1,280 chunks/minute
- Using MPS (Apple Silicon GPU) acceleration

## Database Schema

### Chunk Types

**code_chunk:**
```json
{
  "chunk_id": "sha256(...)",
  "type": "code_chunk",
  "repo_id": "kbhalerao/labcore",
  "file_path": "src/api/views.py",
  "chunk_type": "function",
  "code_text": "def my_function():\n    ...",
  "language": "python",
  "metadata": {
    "commit_hash": "abc123...",
    "commit_date": "2025-01-15T10:30:00",
    "author": "user@example.com",
    "start_line": 42,
    "end_line": 58
  },
  "embedding": [0.123, -0.456, ...],
  "created_at": "2025-11-19T13:32:00"
}
```

**document:**
```json
{
  "chunk_id": "sha256(...)",
  "type": "document",
  "repo_id": "kbhalerao/labcore",
  "file_path": "README.md",
  "doc_type": "markdown",
  "content": "# Project Title\n...",
  "metadata": {
    "commit_hash": "abc123...",
    "commit_date": "2025-01-15T10:30:00",
    "author": "user@example.com"
  },
  "embedding": [0.789, -0.012, ...],
  "created_at": "2025-11-19T13:32:00"
}
```

**commit:**
```json
{
  "chunk_id": "sha256(...)",
  "type": "commit",
  "repo_id": "kbhalerao/labcore",
  "commit_hash": "abc123def456...",
  "commit_date": "2025-01-15T10:30:00",
  "author": "user@example.com",
  "commit_message": "Add new feature...",
  "files_changed": ["src/api/views.py", "tests/test_api.py"],
  "embedding": null,
  "created_at": "2025-11-19T13:32:00"
}
```

## Monitoring

**Check ingestion progress:**
```bash
# Watch log file
tail -f /tmp/final-production-ingestion.log

# Filter for important events
grep "INFO\|ERROR\|SUCCESS" /tmp/final-production-ingestion.log | tail -50

# Check batch progress
grep "Processing batch" /tmp/final-production-ingestion.log | tail -20

# Check database count
curl -s -u Administrator:password123 \
  http://localhost:8091/pools/default/buckets/code_kosha/stats | \
  python3 -c "import sys, json; data=json.load(sys.stdin); print(f\"Chunks: {data['op']['samples']['curr_items'][-1]}\")"
```

## Troubleshooting

**Issue:** Database has duplicates
- **Solution:** Content-hash based IDs prevent this now

**Issue:** Recursion when ingesting code-smriti
- **Solution:** Repos stored outside project in `codesmriti-repos/`

**Issue:** Slow embedding generation
- **Solution:** Using local model with MPS acceleration (~1,280 chunks/min)

**Issue:** Commit messages too large
- **Solution:** Stored separately in commit chunks, referenced by hash

## Future Enhancements

1. **Parallel ingestion** - Process multiple repos concurrently
2. **Smart batching** - Group small repos together
3. **Resume capability** - Continue from last successful repo
4. **Delta updates** - Only process changed repos
5. **Webhook integration** - Auto-ingest on push events
