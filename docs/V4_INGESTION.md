# V4 Ingestion Pipeline

**Version**: 1.1
**Date**: 2025-11-30
**Status**: Production

## Overview

The V4 ingestion pipeline processes repositories into a hierarchical document structure with LLM-generated summaries and vector embeddings. The pipeline handles both **code** and **documentation**:

- **Code**: Parsed with tree-sitter, enriched with LLM summaries, organized bottom-up (symbols → files → modules → repo)
- **Documentation**: Markdown, RST, and text files split semantically by headers

## Quick Start

```bash
cd services/ingestion-worker

# RECOMMENDED: Unified script (code + docs)
./ingest.sh --repo owner/name --dry-run    # Single repo preview
./ingest.sh --repo owner/name              # Single repo full
./ingest.sh --all                          # All repos
./ingest.sh --all --skip-existing          # Resume after failure

# Code only (skip docs)
./ingest.sh --all --code-only --output results.json

# Docs only (after code is indexed)
./ingest.sh --all --docs-only

# Individual scripts (advanced)
python ingest_v4.py --repo owner/name      # Code only
python v4/ingest_docs.py --repo owner/name # Docs only
```

## Pipeline Phases

The unified pipeline executes 6 phases for each repository:

```
Phase 1: Discover Code Files
    └── Scan repo for code extensions (.py, .js, .ts, .svelte, etc.)
    └── Filter out vendor, node_modules, .git, etc.

Phase 2: Process Code Files (parallel)
    └── Parse with tree-sitter → extract symbols
    └── Generate file summary via LLM
    └── Generate symbol summaries for functions/classes >= 5 lines
    └── Track quality metrics

Phase 3: Bottom-Up Aggregation
    └── Group files by directory → create module_summary
    └── Aggregate module summaries → create repo_summary
    └── Set parent/children relationships

Phase 4: Generate Code Embeddings
    └── Embed all code documents using nomic-embed-text (768d)
    └── Batch processing (64 docs/batch)

Phase 5: Store Code Documents
    └── Upsert to Couchbase with document_id deduplication
    └── Delete prior documents for repo (atomic replace)

Phase 6: Documentation Ingestion (ingest_docs.py)
    └── Discover .md, .rst, .txt files
    └── Split by headers using semantic-text-splitter
    └── Generate embeddings per chunk
    └── Store as "document" type records
```

## Document Types Created

| Type | Source | Description |
|------|--------|-------------|
| `file_index` | Code | Source file with symbol list and summary |
| `symbol_index` | Code | Individual function/class (≥5 lines) |
| `module_summary` | Code | Directory-level aggregation |
| `repo_summary` | Code | Repository overview |
| `document` | Docs | Markdown/RST/text chunks split by headers |

## CLI Options

### Unified Script (`ingest.sh`)

| Option | Description | Default |
|--------|-------------|---------|
| `--repo OWNER/NAME` | Ingest single repository | - |
| `--all` | Ingest all repos in REPOS_PATH | - |
| `--dry-run` | Process but don't store to database | False |
| `--code-only` | Skip documentation ingestion | False |
| `--docs-only` | Skip code ingestion | False |
| `--no-llm` | Disable LLM (basic summaries only) | False |
| `--no-embeddings` | Disable embedding generation | False |
| `--skip-existing` | Skip repos with existing V4 documents | False |
| `--llm-provider` | LLM backend: `lmstudio` or `ollama` | lmstudio |
| `--concurrency N` | Parallel file processors | 4 |
| `--output FILE` | Save code results JSON to file | - |

### Code Script (`ingest_v4.py`)

Same options as above except `--code-only` and `--docs-only`.

### Docs Script (`v4/ingest_docs.py`)

| Option | Description |
|--------|-------------|
| `--repo OWNER/NAME` | Process single repo (default: all) |
| `--dry-run` | Preview without writing |

## Configuration

### Environment Variables

Set in `.env` or export before running:

```bash
# Required
REPOS_PATH=/path/to/cloned/repos    # Directory containing owner_name folders
COUCHBASE_HOST=localhost
COUCHBASE_BUCKET=code_kosha
COUCHBASE_USERNAME=Administrator
COUCHBASE_PASSWORD=your_password

# Optional
SUPPORTED_CODE_EXTENSIONS=.py,.js,.ts,.tsx,.jsx,.go,.rs,.java
```

### Repository Structure

Repos must be cloned with folder naming `owner_repo`:

```
$REPOS_PATH/
├── kbhalerao_labcore/          # → repo_id: kbhalerao/labcore
│   └── .git/
├── other-org_their-app/        # → repo_id: other-org/their-app
│   └── .git/
└── ...
```

### LLM Provider Setup

#### LM Studio (Recommended)

1. Download [LM Studio](https://lmstudio.ai)
2. Load a model (e.g., `qwen2.5-coder-7b-instruct`)
3. Start local server on port 1234
4. Run with `--llm-provider lmstudio`

#### Ollama

1. Install Ollama: `brew install ollama`
2. Pull model: `ollama pull qwen2.5-coder:7b`
3. Run with `--llm-provider ollama`

## Performance

Based on production run (101 repos, Nov 2025):

| Metric | Value |
|--------|-------|
| Total duration | 32 hours |
| Repos processed | 101 |
| Files indexed | 13,358 |
| Symbols indexed | 31,334 |
| Documents created | 48,795 |
| LLM tokens used | 24.7M (estimated input+output) |
| **Tokens per file** | ~1,850 |
| **Tokens per document** | ~550 |
| Avg time per repo | ~20 min |

### Resource Requirements

- **CPU**: Multi-core recommended (parallel file processing)
- **RAM**: 16GB+ (LLM model + embeddings)
- **GPU**: Apple Silicon MPS or CUDA for embeddings
- **Disk**: ~500MB per 1000 documents in Couchbase

## Output Format

With `--output results.json`:

```json
{
  "timestamp": "2025-11-30T07:10:10.338990",
  "duration_seconds": 115861.88,
  "summary": {
    "total": 101,
    "success": 99,
    "failed": 0,
    "skipped": 2
  },
  "results": [
    {
      "repo_id": "owner/repo",
      "commit_hash": "abc123...",
      "files_discovered": 320,
      "documents_stored": {
        "file_index": 317,
        "symbol_index": 280,
        "module_summary": 113,
        "repo_summary": 1
      },
      "quality": {
        "stats": {
          "duration_seconds": 1880.04,
          "files": { "processed": 317, "failed": 0, "skipped": 3 },
          "symbols_processed": 280,
          "modules_created": 113,
          "llm": {
            "calls": 711,
            "successes": 711,
            "failures": 0,
            "success_rate": 1.0,
            "tokens_used": 415741
          }
        }
      },
      "status": "success"
    }
  ]
}
```

## Monitoring

### During Ingestion

Watch progress in real-time:

```bash
# Shows per-file progress
python ingest_v4.py --repo owner/name 2>&1 | tee ingestion.log

# Output format:
# [123/450] src/auth/models.py (ok, 5 symbols)
# [124/450] src/auth/views.py (ok, 12 symbols)
# [125/450] src/utils/helpers.py (skip, 0 symbols)
```

### Post-Ingestion Verification

```sql
-- Count V4 documents by type
SELECT type, COUNT(*) as count
FROM `code_kosha`
WHERE version.schema_version = 'v4.0'
GROUP BY type;

-- Verify specific repo
SELECT type, COUNT(*) as count
FROM `code_kosha`
WHERE repo_id = 'owner/repo'
  AND version.schema_version = 'v4.0'
GROUP BY type;

-- Check for missing embeddings
SELECT COUNT(*) as missing_embeddings
FROM `code_kosha`
WHERE version.schema_version = 'v4.0'
  AND embedding IS MISSING;
```

## Troubleshooting

### Common Issues

**LLM Connection Failed**
```
Error: Connection refused to localhost:1234
```
- Ensure LM Studio/Ollama is running
- Check correct port in `llm_enricher.py` config

**Out of Memory**
```
Error: CUDA out of memory / MPS out of memory
```
- Reduce `--concurrency` to 2
- Use smaller LLM model
- Run with `--no-embeddings` then embed separately

**Couchbase Connection Failed**
```
Error: Could not connect to Couchbase
```
- Verify Couchbase is running: `docker-compose ps`
- Check credentials in `.env`

### Resume After Failure

```bash
# Skip repos that already have V4 documents
python ingest_v4.py --all --skip-existing --output /tmp/resume.json
```

### Re-ingest Single Repo

```bash
# Will delete existing documents first
python ingest_v4.py --repo owner/name
```

## Architecture

```
services/ingestion-worker/
├── ingest.sh                 # UNIFIED: Runs code + docs ingestion
├── ingest_v4.py              # Code ingestion CLI
├── config.py                 # Environment config
├── llm_enricher.py           # LLM provider configs
├── llm_chunker.py            # Semantic chunking for underchunked files
│
├── v4/
│   ├── pipeline.py           # Code pipeline orchestrator
│   ├── schemas.py            # Document dataclasses
│   ├── file_processor.py     # File → FileIndex + SymbolIndex
│   ├── aggregator.py         # Bottom-up summary aggregation
│   ├── llm_enricher.py       # V4-specific LLM prompts
│   ├── quality.py            # Quality tracking
│   └── ingest_docs.py        # Documentation ingestion
│
├── parsers/
│   ├── code_parser.py        # Tree-sitter parsing
│   └── document_parser.py    # Markdown/RST parsing
│
├── embeddings/
│   └── local_generator.py    # nomic-embed-text-v1.5
│
└── storage/
    └── couchbase_client.py   # V4 storage methods
```

## Incremental Updates

For ongoing maintenance after initial ingestion, use `incremental_v4.py`:

```bash
cd services/ingestion-worker

# Full incremental update (all repos)
python incremental_v4.py

# Single repo
python incremental_v4.py --repo owner/name

# Dry run - shows what would change, runs LLM for summary comparison
python incremental_v4.py --dry-run

# Adjust threshold for full re-ingestion (default: 5%)
python incremental_v4.py --threshold 0.10
```

### How It Works

```
Phase 1: Repository Discovery
  - Get canonical repo list (GitHub API or config file)
  - Compare to: repos on disk, repos in database
  - Identify: new, existing, orphaned repos

Phase 2: Clone New Repos
  - git clone --depth 1 for repos not on disk

Phase 3: Clean Orphaned Repos
  - DELETE FROM code_kosha WHERE repo_id = X
  - For repos no longer in canonical list

Phase 4: Process Repos
  - git fetch origin
  - Compare origin HEAD to stored commit (in repo_summary)
  - If unchanged: skip
  - If >threshold% files changed: full re-ingest
  - Otherwise: surgical update
    - Delete docs for deleted files
    - Process only changed files
    - Regenerate affected module_summary + repo_summary
```

### Incremental CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--repo OWNER/NAME` | Process single repo | All |
| `--dry-run` | Preview + run LLM, skip DB writes | False |
| `--threshold N` | Full re-ingest if >N% files changed | 0.05 (5%) |
| `--no-llm` | Disable LLM summaries | False |
| `--llm-provider` | lmstudio or ollama | lmstudio |

### Canonical Repo Sources

The incremental updater checks these sources in order:

1. **GitHub API** - If `GITHUB_TOKEN` is set in `.env`
2. **Config file** - `repos_to_ingest.txt` (one repo per line)
3. **Disk fallback** - Whatever repos exist in `$REPOS_PATH`

## Related Documentation

- [V4_SCHEMA_SPEC.md](V4_SCHEMA_SPEC.md) - Document schema specification
- [V4_RAG_MIGRATION.md](V4_RAG_MIGRATION.md) - API migration guide
- [V4_RAG_TOOLS_DESIGN.md](V4_RAG_TOOLS_DESIGN.md) - MCP/RAG tool design
