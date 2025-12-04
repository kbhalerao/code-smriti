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
