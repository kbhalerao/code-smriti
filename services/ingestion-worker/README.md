# CodeSmriti Ingestion Worker

Ingests GitHub repositories into Couchbase vector database with semantic embeddings.

## Prerequisites

- **Python 3.9+** with `uv` package manager
- **Couchbase** running (localhost:8091 or configured host)
- **GitHub token** in `../../.env` for private repos
- **tmux** for persistent sessions (optional but recommended)

## Setup

### 1. Install Dependencies

The venv is managed with `uv` (not standard venv):

```bash
# Install uv if not already installed
brew install uv  # macOS

# Install dependencies into the existing venv
/opt/homebrew/bin/uv pip install --python venv/bin/python3 -r requirements.txt
```

### 2. Configure Environment

Required environment variables:

```bash
# GitHub repos to ingest (comma-separated)
GITHUB_REPOS="owner/repo1,owner/repo2"

# GitHub token (automatically loaded from ../../.env)
GITHUB_TOKEN="ghp_..."

# Embedding backend
EMBEDDING_BACKEND=local  # Uses nomic-ai/nomic-embed-text-v1.5 with MPS/GPU

# Couchbase connection
COUCHBASE_HOST=localhost
COUCHBASE_USERNAME=Administrator
COUCHBASE_PASSWORD=password123
COUCHBASE_BUCKET=code_kosha

# Repo storage (must be writable)
REPOS_PATH=/tmp/repos

# Logging
LOG_LEVEL=INFO
```

## Running Ingestion

### Quick Start (Recommended)

Use the provided launch script:

```bash
./run-ingestion.sh
```

This starts ingestion in a persistent tmux session named `code-ingestion`.

### Manual Launch

If you need to customize:

```bash
# Load GitHub token from .env
source ../../.env

# Start in tmux (persistent session)
tmux new-session -d -s "code-ingestion" "
  GITHUB_TOKEN='${GITHUB_TOKEN}' \
  GITHUB_REPOS='owner/repo1,owner/repo2' \
  EMBEDDING_BACKEND=local \
  COUCHBASE_HOST=localhost \
  COUCHBASE_USERNAME=Administrator \
  COUCHBASE_PASSWORD=password123 \
  COUCHBASE_BUCKET=code_kosha \
  REPOS_PATH=/tmp/repos \
  LOG_LEVEL=INFO \
  venv/bin/python3 -u worker.py 2>&1 | tee /tmp/ingestion-\$(date +%Y%m%d-%H%M%S).log
"
```

## Monitoring

### Watch Progress

```bash
# Attach to tmux session
tmux attach -t code-ingestion

# Or tail the log file
tail -f /tmp/ingestion-*.log

# Or monitor in real-time
watch -n 5 'tail -30 /tmp/ingestion-*.log | grep -E "INFO|Processing|✓|chunks"'
```

### Check Status

```bash
# Count chunks processed
grep "Parsed.*chunks" /tmp/ingestion-*.log | tail -5

# Check for errors
grep "ERROR" /tmp/ingestion-*.log

# See batch processing
grep "Processing batch" /tmp/ingestion-*.log | tail -20
```

### Stop Ingestion

```bash
# Kill tmux session
tmux kill-session -t code-ingestion

# Or detach and let it run
# (Ctrl+B, then D while attached)
```

## Configuration

### Chunking Strategy

Current: Class and function-level chunking with 6000 char limit
- Classes > 6KB are truncated (⚠️ loses context)
- Functions extracted individually
- Each chunk gets semantic embedding

### Performance Tuning

In `embeddings/local_generator.py`:

```python
batch_size = 128  # Chunks per embedding batch (MPS GPU optimized)
```

In `parsers/code_parser.py`:

```python
MAX_CHUNK_SIZE = 6000  # chars (~4500-7500 tokens, safe for Nomic 8192 limit)
```

### Database Schema

Chunks are stored with:
- `chunk_id`: SHA256 hash (repo:file:commit:content_hash)
- `type`: "code_chunk", "document_chunk", or "commit_chunk"
- `embedding`: 768-dim vector (nomic-embed-text-v1.5)
- `metadata`: File path, class/function name, line numbers, git info

## Troubleshooting

### "ModuleNotFoundError: No module named 'loguru'"

Your venv doesn't have dependencies. Install with:

```bash
/opt/homebrew/bin/uv pip install --python venv/bin/python3 -r requirements.txt
```

### "Read-only file system: '/repos'"

Set a writable REPOS_PATH:

```bash
export REPOS_PATH=/tmp/repos
```

### "Authentication failed for GitHub"

Ensure GITHUB_TOKEN is set:

```bash
source ../../.env
echo $GITHUB_TOKEN  # Should show ghp_...
```

### "Cannot connect to Couchbase"

Check Couchbase is running:

```bash
docker ps | grep couchbase
# Or
curl -u Administrator:password123 http://localhost:8091/pools
```

### Large classes getting truncated

Current issue - see Issue #XXX. Options:
1. Method-level chunking (extract each method separately)
2. Skip truncation (let embedding fail gracefully)
3. Hierarchical chunking (header + methods)

## Development

### Architecture

```
worker.py              # Main orchestrator
├── parsers/
│   ├── code_parser.py       # Python/JS/TS parsing (tree-sitter)
│   ├── document_parser.py   # Markdown/README parsing
│   └── commit_parser.py     # Git commit message extraction
├── embeddings/
│   └── local_generator.py   # Nomic embedding generation (MPS GPU)
└── storage/
    └── couchbase_client.py  # Vector DB client
```

### Key Optimizations

- **Streaming writes**: Batches written immediately after embedding (no memory buildup)
- **MPS GPU**: Apple Silicon acceleration for embeddings
- **Batch size 128**: Optimal for 6KB chunks with MPS
- **Content-hash IDs**: Enables file-level incremental updates
- **Skip minified files**: `.min.js`, `.min.css` excluded

### Testing

```bash
# Test single repo
GITHUB_REPOS="test/small-repo" venv/bin/python3 worker.py

# Dry run (parse only, no DB)
# (Not implemented - would need --dry-run flag)
```

## Production Deployment

### Full Ingestion (Initial Setup)

When ingesting all repositories for the first time:

1. **Update `.env` file** with all repos in `GITHUB_REPOS` (comma-separated)
2. **Launch ingestion** with `./run-ingestion.sh` (creates tmux session)
3. **Monitor progress** with `tail -f /tmp/ingestion-*.log`
4. **Verify completion** by checking the final log summary

For ~80 repositories with mixed sizes, expect several hours for full ingestion with:
- 10 concurrent file workers
- 128-batch embedding generation
- MPS GPU acceleration on Apple Silicon

### Incremental Updates (Automated)

**Cron job** runs daily at 8:00 AM:
```bash
0 8 * * * /Users/kaustubh/Documents/code/code-smriti/3-maintain/run-incremental-update
```

This performs **file-level incremental updates**:
- Detects changed files via git commit hash
- Re-generates embeddings only for modified files
- Replaces all chunks for changed files (content-hash based IDs)
- Keeps unchanged files intact (no unnecessary re-processing)

### Vector Search Quality

Evaluation results (tested on 37 questions across 5 repos):
- **Repo-level Recall@5**: 90%
- **File-level Recall@5**: 30%
- **MRR**: 0.20
- **Top-5 success rate**: 40%

These metrics are acceptable for RAG use cases because:
- Hybrid results (documentation + code) provide valuable context
- Top 10-20 results give enough material for narrative generation
- File citations allow iterative refinement

### Important Learnings

1. **Environment variables**: The `.env` file takes precedence over command-line exports
   - To change repos: Edit `GITHUB_REPOS` in `../../.env` directly
   - Don't rely on `export GITHUB_REPOS=...` with the run script

2. **Vector search**: Use Couchbase FTS (not N1QL ANN function)
   - FTS API: `VectorQuery` + `VectorSearch.from_vector_query()`
   - Search returns IDs + scores; fetch full documents separately

3. **Async pipeline**: 10 concurrent files + 4 parsing threads provides good throughput
   - Configured in `worker.py:__init__` (lines 56-60)
   - Adjust based on system resources

4. **Incremental updates**: Content-hash chunk IDs enable file-level granularity
   - Changed files fully replaced (all chunks regenerated)
   - Unchanged files skip re-processing entirely

## Log Files

Logs are written to `/tmp/ingestion-YYYYMMDD-HHMMSS.log` with:
- Repository cloning progress
- Parsing stats (chunks per file)
- Embedding generation (batch progress)
- Database writes (upsert confirmations)
- Warnings (truncated chunks, skipped files)
- Errors (failed repos, connection issues)
