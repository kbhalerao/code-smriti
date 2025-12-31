# Claude Code Project Instructions

## Python Environment

This project uses **uv** for Python package management. Always use `uv run` to execute Python scripts:

```bash
# Correct way to run Python scripts
uv run python script.py

# Run from specific service directory
cd services/ingestion-worker && uv run python -c "..."
```

Do NOT use:
- `python` directly (may use wrong environment)
- `source venv/bin/activate` or `source .venv/bin/activate`
- `pip install` (use `uv add` or `uv pip install` instead)

## Couchbase Database

Credentials are in `.env` at project root. The bucket is `code_kosha` (not `code-smriti`).

## Embedding Pipeline

All embeddings must be **normalized to unit length** (L2 norm = 1.0) for the FTS vector index which uses `dot_product` similarity.

Key files:
- `services/ingestion-worker/embeddings/local_generator.py` - Core embedding generation
- `services/api-server/app/rag/tools.py` - Search query embeddings

Use `search_document:` prefix for indexed documents and `search_query:` prefix for search queries.

## Local LLM Setup (LM Studio)

LM Studio provides local LLM inference on port 1234, proxied via nginx at `/llm/*`.

### Auto-start Configuration

**LaunchAgent**: `~/Library/LaunchAgents/com.lmstudio.server.plist`
**Startup script**: `~/.lmstudio/startup.sh`

The startup script:
1. Starts LM Studio server with `--bind 0.0.0.0 --cors`
2. Loads models with specified context lengths (skips if already loaded)

### Models & Context Lengths

| Model | Context | Size |
|-------|---------|------|
| qwen/qwen3-30b-a3b-2507 | 128K | 17 GB |
| qwen/qwen3-next-80b | 128K | 45 GB |
| ibm/granite-4-h-tiny | 16K | 4 GB |
| text-embedding-nomic-embed-text-v1.5 | default | 84 MB |

### Troubleshooting After Power Failure

If LLM proxy returns 502:
1. Check LM Studio is running: `lms status`
2. Start if needed: `lms server start --port 1234 --bind 0.0.0.0 --cors`
3. Load models via GUI (CLI `lms load` may fail if LM Studio app isn't open)
4. If Colima's `host.docker.internal` is stale, restart Colima: `colima stop && colima start`

### Manual Commands

```bash
# Check status
lms status

# Start server
lms server start --port 1234 --bind 0.0.0.0 --cors

# Load model with context
lms load qwen/qwen3-30b-a3b-2507 --context-length 131072 --yes

# Test from host
curl http://localhost:1234/v1/models

# Test via nginx proxy
curl http://localhost/llm/v1/models
```
