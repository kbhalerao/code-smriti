# Instructions for Claude Code on MacStudio

These are instructions for the Claude Code instance running on macstudio.local to execute the embedding analysis and chunking improvements.

---

## ⚠️ IMPORTANT: DISABLE AUTO-UPDATES FIRST

Before running any analysis or re-indexing, **disable the cron job** that auto-updates chunks on commit changes. This prevents clobbering expensive LLM-enriched work.

```bash
# Check current cron jobs
crontab -l

# Edit and comment out the ingestion worker cron
crontab -e
# Add # before the ingestion line to disable it

# Or if using launchd on macOS
launchctl unload ~/Library/LaunchAgents/com.codesmriti.ingestion.plist

# Verify it's disabled
crontab -l | grep -v "^#" | grep ingestion
```

**DO NOT re-enable until V3 migration is complete and versioning is in place.**

---

## Context

We're improving CodeSmriti's chunking strategy. The current implementation has issues:
1. Chunks are often fragments (class definitions without implementation)
2. Code is stored redundantly at multiple levels
3. No LLM-assisted summaries
4. Unknown embedding space diversity

New files created in `lib/ingestion-worker/`:
- `CHUNKING_V2_DESIGN.md` - Architecture document
- `CHUNKING_V3_NORMALIZED.md` - Non-redundant schema
- `chunk_versioning.py` - **Versioning system to protect LLM work**
- `audit_chunks.py` - Quality audit tool
- `analyze_embeddings.py` - PCA/clustering analysis
- `llm_enricher.py` - LLM integration (configured for macstudio.local:1234)
- `ingest_v2.py` - New ingestion pipeline

### Chunk Versioning

All new chunks will include version metadata:
```python
"version": {
    "schema_version": "v3.0",
    "pipeline_version": "2025.11.24",
    "enrichment_level": "llm_full",  # none | basic | llm_summary | llm_full
    "enrichment_cost": 2500,         # estimated tokens used
    "protect_from_update": true,     # prevents cron clobbering
    "created_at": "2025-11-24T20:00:00Z"
}
```

**LLM-enriched chunks are automatically protected from updates.**

---

## Step 1: Run Embedding Analysis

First, analyze the current embedding space to understand diversity and find patterns.

```bash
cd /path/to/code-smriti/lib/ingestion-worker

# Install dependencies if needed
pip install scikit-learn matplotlib

# Run analysis on sample of 10k chunks
python analyze_embeddings.py --sample 10000 --export embedding_analysis.json --visualize embedding_space.png

# Or analyze specific repo
python analyze_embeddings.py --repo kbhalerao/labcore --sample 5000 --export labcore_analysis.json
```

Expected output:
- PCA variance explained (how many dimensions needed for 90%?)
- Cluster analysis (how many distinct patterns?)
- Near-duplicate detection (how much redundancy?)
- Visualization of embedding space

---

## Step 2: Run Quality Audit

Audit existing chunks to identify quality issues.

```bash
# Audit all repos (samples 100 chunks per repo)
python audit_chunks.py --export audit_report.json

# Audit specific repo in detail
python audit_chunks.py --repo kbhalerao/labcore --sample 500
```

Look for:
- `empty_content` issues (high severity)
- `fragment_only` issues (class definitions without bodies)
- `missing_embedding` issues
- Quality distribution (high/medium/low/critical)

---

## Step 3: Test LLM Enrichment

Test that LLM Studio is accessible and working.

```bash
# Test the enricher
python llm_enricher.py
```

This should:
1. Connect to macstudio.local:1234
2. Send a test prompt
3. Return a structured analysis

If it fails, check:
- LM Studio is running
- Model is loaded (qwen3-3b or similar)
- Port 1234 is accessible

---

## Step 4: Test V2 Ingestion on Small Repo

Test the new pipeline on a small repository first.

```bash
# Without LLM (just hierarchy and quality scoring)
python ingest_v2.py --repo kbhalerao/claudegram

# With LLM enrichment
python ingest_v2.py --repo kbhalerao/claudegram --enrich --model qwen3-3b
```

Check output for:
- Module detection working
- Repo/module summaries created
- Quality scores calculated
- LLM enrichment (if enabled)

---

## Step 5: Report Back

After running the analyses, report:

1. **Embedding Analysis Results**:
   - Effective dimensionality (dimensions for 90% variance)
   - Number of natural clusters
   - Silhouette score (clustering quality)
   - Number of near-duplicates found

2. **Audit Results**:
   - Total chunks audited
   - Quality distribution (% high/medium/low/critical)
   - Top issue types
   - Worst repositories

3. **LLM Test Results**:
   - Connection successful?
   - Response quality?
   - Latency?

4. **V2 Test Results**:
   - Chunks created
   - Quality scores
   - Time taken

---

## Configuration Notes

### LLM Studio Endpoint
The `llm_enricher.py` is configured to use:
```python
LMSTUDIO_CONFIG = LLMConfig(
    provider="lmstudio",
    model="qwen3-3b",
    base_url="http://macstudio.local:1234",
    temperature=0.3
)
```

If using a different model, update the model name.

### Couchbase Connection
Uses config from `config.py`:
- `COUCHBASE_HOST`
- `COUCHBASE_USERNAME`
- `COUCHBASE_PASSWORD`
- `COUCHBASE_BUCKET`

Ensure these are set correctly.

### Repos Path
V2 ingestion looks for repos at:
```python
repos_base = config.repos_path if hasattr(config, 'repos_path') else "/repos"
```

Set `REPOS_PATH` environment variable if different.

---

## Troubleshooting

### "tree-sitter-languages not available"
```bash
pip install tree-sitter-languages
```

### Couchbase connection failed
Check that Couchbase is running and accessible from macstudio.

### LLM timeout
Increase timeout in `llm_enricher.py`:
```python
self.client = httpx.AsyncClient(timeout=180.0)  # 3 minutes
```

### Out of memory on embedding analysis
Reduce sample size:
```bash
python analyze_embeddings.py --sample 5000
```

---

## Next Steps Based on Results

### If high redundancy found:
- Implement V3 normalized schema
- Store line numbers instead of code
- Create code fetcher service

### If low clustering quality:
- Embeddings may be too generic
- Consider domain-specific embedding model
- Or add code patterns to embedding text

### If many fragment-only issues:
- Tree-sitter parsing needs fixing
- Ensure full class bodies are extracted
- Check for regex fallback triggering

### If LLM enrichment works well:
- Run batch enrichment on high-importance files
- Create repo and module summaries for all repos
- Add quality scores to all chunks
