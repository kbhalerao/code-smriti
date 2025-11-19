# 2. Initialize

Run the initial ingestion to populate your knowledge base.

## Quick Start

```bash
# 1. Run pipeline (this takes a while for many repos)
./run-pipeline

# 2. Create vector search index
./create-index

# 3. Verify data loaded correctly
./verify-data
```

## Scripts

### run-pipeline

Processes all repositories from `1-config/repos-to-ingest.txt`.

**What it does:**
1. Clones repositories (or pulls updates)
2. Parses code into semantic chunks (functions, classes)
3. Parses documentation (README, docs/)
4. Generates vector embeddings (768-dimensional)
5. Stores chunks in Couchbase

**Expected time:**
- Small repos (~25 chunks): 1-2 seconds
- Medium repos (~50 chunks): 2-3 seconds
- Large repos (50K+ chunks): 10-15 minutes

**Monitoring:**
```bash
# In another terminal
cd ../3-maintain
./monitor
```

### create-index

Creates the HNSW vector search index for fast similarity search.

**What it does:**
1. Builds multi-layer graph structure
2. Connects vectors to nearest neighbors
3. Enables sub-50ms vector queries

**Expected time:** ~1-2 minutes for 40K chunks

### verify-data

Checks that everything worked:
- Couchbase is running
- Database contains chunks
- Vector index exists

## Run in Background (tmux)

For long-running ingestion:

```bash
# Start tmux session
tmux new -s ingestion

# Run pipeline
./run-pipeline

# Detach: Ctrl+b, then d

# Reattach later
tmux attach -t ingestion
```

## Troubleshooting

**Pipeline fails on a repo:**
- Check GitHub token has access
- Repo might be archived or deleted
- Mark as `[DONE]` in repos-to-ingest.txt to skip

**Out of memory:**
- Reduce batch size in `.env`
- Process fewer repos at once
- Add more RAM (16GB recommended minimum)

**Index creation fails:**
- Check Couchbase FTS service is running
- Verify port 8094 is accessible
- Check logs: `docker logs codesmriti_couchbase`

## Performance Tips

**Use local embeddings:**
```bash
EMBEDDING_BACKEND=local  # 10-20x faster than Ollama API
```

**MPS acceleration (Apple Silicon):**
Automatically detected and used for ~1,280 chunks/minute.

## Next Step

→ **[3-maintain](../3-maintain/README.md)** - Keep your knowledge base fresh
