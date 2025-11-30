# V4 Ingestion Pipeline

**Version**: 1.0
**Date**: 2025-11-30
**Status**: Production

## Overview

The V4 ingestion pipeline processes repositories into a hierarchical document structure with LLM-generated summaries and vector embeddings. Documents are organized bottom-up: symbols aggregate into files, files into modules, modules into repo summary.

## Quick Start

```bash
cd services/ingestion-worker

# Single repo (dry run first)
python ingest_v4.py --repo owner/name --dry-run

# Single repo (full ingestion)
python ingest_v4.py --repo owner/name

# All repos in REPOS_PATH
python ingest_v4.py --all

# Resume after failure (skip already indexed)
python ingest_v4.py --all --skip-existing

# Save results to JSON
python ingest_v4.py --all --output /tmp/results.json
```

## Pipeline Phases

The pipeline executes 5 phases for each repository:

```
Phase 1: Discover Files
    └── Scan repo for supported extensions (.py, .js, .ts, etc.)
    └── Filter out vendor, node_modules, .git, etc.

Phase 2: Process Files (parallel)
    └── Parse with tree-sitter → extract symbols
    └── Generate file summary via LLM
    └── Generate symbol summaries for functions/classes >= 5 lines
    └── Track quality metrics

Phase 3: Bottom-Up Aggregation
    └── Group files by directory → create module_summary
    └── Aggregate module summaries → create repo_summary
    └── Set parent/children relationships

Phase 4: Generate Embeddings
    └── Embed all documents using nomic-embed-text (768d)
    └── Batch processing (64 docs/batch)

Phase 5: Store Documents
    └── Upsert to Couchbase with document_id deduplication
    └── Delete prior documents for repo (atomic replace)
```

## CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--repo OWNER/NAME` | Ingest single repository | - |
| `--all` | Ingest all repos in REPOS_PATH | - |
| `--dry-run` | Process but don't store to database | False |
| `--no-llm` | Disable LLM (basic summaries only) | False |
| `--no-embeddings` | Disable embedding generation | False |
| `--skip-existing` | Skip repos with existing V4 documents | False |
| `--llm-provider` | LLM backend: `lmstudio` or `ollama` | lmstudio |
| `--concurrency N` | Parallel file processors | 4 |
| `--output FILE` | Save results JSON to file | - |

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
├── jayp-eci_pinionfe/          # → repo_id: jayp-eci/pinionfe
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
├── ingest_v4.py              # CLI entry point
├── config.py                 # Environment config
├── llm_enricher.py           # LLM provider configs
└── v4/
    ├── pipeline.py           # Main orchestrator
    ├── schemas.py            # Document dataclasses
    ├── file_processor.py     # File → FileIndex + SymbolIndex
    ├── aggregator.py         # Bottom-up summary aggregation
    ├── llm_enricher.py       # V4-specific LLM prompts
    └── quality.py            # Quality tracking
```

## Related Documentation

- [V4_SCHEMA_SPEC.md](V4_SCHEMA_SPEC.md) - Document schema specification
- [V4_RAG_MIGRATION.md](V4_RAG_MIGRATION.md) - API migration guide
- [V4_RAG_TOOLS_DESIGN.md](V4_RAG_TOOLS_DESIGN.md) - MCP/RAG tool design
