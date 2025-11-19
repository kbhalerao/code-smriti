# CodeSmriti - TODO List

## Completed ✓

### Infrastructure Setup
- [x] Docker compose setup for Couchbase, MCP server, ingestion worker
- [x] Native Python execution for ingestion (bypasses Docker RAM limits)
- [x] Auto-restart on reboot for persistent services
- [x] External nginx reverse proxy documentation
- [x] Helper scripts for cloning repos and running ingestion
- [x] Tree-sitter version pinning (0.20.4)
- [x] Embedding model revision pinning (eliminates re-download warnings)

### Ingestion Pipeline
- [x] Code parser (Python, JavaScript, TypeScript) using tree-sitter
- [x] Document parser (Markdown, JSON, YAML, TXT)
- [x] Embedding generation (nomic-embed-text-v1.5, 768 dimensions)
- [x] Batch processing with memory optimization
- [x] Progress logging every 100 batches
- [x] Git metadata extraction (author, commit hash, last modified)
- [x] CodeChunk and DocumentChunk data structures
- [x] Couchbase client with batch upsert
- [x] Repository cloning and updating
- [x] Configurable repos path (Docker vs native)

---

## High Priority - Next Steps

### 1. Couchbase Vector Search Setup (CRITICAL for RAG)
**Status**: Not implemented
**Blocker**: Cannot do semantic code search without this

**Tasks**:
- [ ] Create vector search index in Couchbase
  - Index name: `code_embeddings_index`
  - Vector field: `embedding`
  - Dimensions: 768
  - Similarity: cosine
- [ ] Add index creation to setup scripts
- [ ] Verify index creation via Couchbase Web UI (http://localhost:8091)
- [ ] Test vector similarity search with sample query

**Couchbase Index SQL** (run in Query Workbench):
```sql
CREATE INDEX code_embeddings_index
ON `code_memory`(embedding VECTOR)
USING GSI
WITH {
  "dimension": 768,
  "similarity": "cosine",
  "description": "Vector index for semantic code search"
};
```

### 2. Test End-to-End Ingestion
**Status**: Storage layer implemented but not tested
**Blocker**: Need to verify data is actually being stored correctly

**Tasks**:
- [ ] Run native ingestion on a small test repo first (NOT labcore - too big)
- [ ] Verify chunks are stored in Couchbase Web UI
  - Check Buckets > code_memory > Documents
  - Verify embedding vectors are present (768 floats)
  - Check metadata fields (repo_id, file_path, chunk_type, etc.)
- [ ] Run query to count total chunks by type:
  ```sql
  SELECT type, COUNT(*) as count
  FROM `code_memory`
  GROUP BY type
  ```
- [ ] Fix any storage errors that come up
- [ ] Once working, run full ingestion on labcore (55K+ chunks)

### 3. RAG Implementation (MCP Server)
**Status**: MCP server skeleton exists but no search/retrieval
**Location**: `/mcp-server/server.py`

**Tasks**:
- [ ] Implement `search_code` tool
  - Input: Natural language query
  - Generate query embedding using same nomic model
  - Vector search against Couchbase index
  - Return top K most similar code chunks
  - Include similarity scores
- [ ] Implement `search_docs` tool (similar to search_code)
- [ ] Add filtering options:
  - By repository
  - By file extension
  - By code type (function, class, etc.)
- [ ] Context window management
  - Combine multiple chunks intelligently
  - Respect token limits for Claude
- [ ] Response formatting
  - Include file paths with line numbers
  - Show similarity scores
  - Provide surrounding context

### 4. Multi-Repository Ingestion
**Status**: Single repo works, need to add more

**Tasks**:
- [ ] Add more repositories to GITHUB_REPOS in .env
- [ ] Test incremental ingestion (updating existing repos)
- [ ] Handle repo deletions (cleanup old chunks)
- [ ] Add repo metadata tracking:
  - Last ingested timestamp
  - Commit hash at ingestion time
  - Number of chunks per repo

---

## Medium Priority

### 5. Ingestion Optimization
- [ ] Investigate faster embedding generation
  - GPU support for SentenceTransformer?
  - Batch size tuning (currently 8)
- [ ] Parallel repository processing (process multiple repos concurrently)
- [ ] Incremental updates (only re-embed changed files)
- [ ] Add deduplication (skip identical code chunks)

### 6. Monitoring and Observability
- [ ] Add metrics collection
  - Chunks processed per second
  - Embedding generation time
  - Couchbase upsert latency
- [ ] Better error handling and retry logic
- [ ] Health check endpoint for MCP server
- [ ] Ingestion status dashboard
  - Which repos are indexed
  - Last update time
  - Total chunks count

### 7. Query Performance
- [ ] Add caching layer for frequent queries
- [ ] Implement hybrid search (vector + keyword)
- [ ] Add re-ranking for better results
- [ ] Query result pagination

---

## Low Priority / Future Enhancements

### 8. Automation
- [ ] Cron job for periodic re-ingestion (daily/weekly)
- [ ] GitHub webhook integration (auto-update on push)
- [ ] Slack/Discord notifications on ingestion completion

### 9. Advanced Features
- [ ] Code-to-code similarity search
- [ ] Function call graph analysis
- [ ] Dependency tracking
- [ ] Multi-language support (Go, Rust, Java, etc.)
- [ ] Image/diagram embedding (for architecture docs)

### 10. Security & Access Control
- [ ] API key authentication for MCP server
- [ ] Per-repository access control
- [ ] Rate limiting
- [ ] Audit logging

---

## Known Issues

### 1. Model Re-Download Warnings
**Status**: FIXED ✓
- Added revision pinning in embeddings/generator.py

### 2. Docker Memory Limits
**Status**: WORKAROUND ✓
- Running ingestion natively outside Docker to access full 256GB RAM
- Colima limited to 7.7GB, not enough for large repos

### 3. GitHub Token Format Warnings
**Status**: PARTIAL FIX
- Token validation added but still shows warnings for some token types
- Using local clones instead of API cloning (workaround)

---

## Testing Checklist

Before considering the system "production ready":

- [ ] Test ingestion with 5+ different repositories
- [ ] Verify vector search returns relevant results
- [ ] Test concurrent ingestion (multiple repos in parallel)
- [ ] Stress test with 100K+ chunks
- [ ] Test RAG with real Claude queries
- [ ] Verify incremental updates work correctly
- [ ] Test error recovery (network failures, Couchbase restarts)
- [ ] Performance benchmark:
  - Embedding generation speed
  - Storage throughput
  - Query latency (p50, p95, p99)

---

## Documentation Needed

- [ ] API documentation for MCP tools
- [ ] Example queries and use cases
- [ ] Troubleshooting guide
- [ ] Performance tuning guide
- [ ] Backup and recovery procedures

---

**Last Updated**: 2024-11-18 (via Claude Code session)

**Next Session**: Start with #2 (Test End-to-End Ingestion) using a small test repo
