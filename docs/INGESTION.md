# Ingestion Worker Guide

The ingestion worker processes repositories, parses code, and generates embeddings. Due to the resource-intensive nature of embedding generation, it's configured to run **on-demand** rather than automatically.

## Quick Start

### Run Ingestion Manually

```bash
# Run in background
./run-ingestion.sh

# Or run with live logs
./run-ingestion.sh follow
```

### Monitor Progress

```bash
# Follow logs
docker-compose logs -f ingestion-worker

# Check if still running
docker-compose ps ingestion-worker
```

## How It Works

The ingestion process has these stages:

1. **Repository Cloning/Updating** (~10 seconds)
   - Clones or pulls latest changes from GitHub
   - Or uses pre-cloned repos from `./repos/` directory

2. **Code Parsing** (~30 seconds for large repos)
   - Uses tree-sitter to parse code into semantic chunks
   - Extracts functions, classes, and code blocks
   - Example: 55,325 code chunks from a large repo

3. **Documentation Parsing** (~2 seconds)
   - Parses markdown, JSON, YAML files
   - Extracts hashtags and metadata
   - Example: 187 document chunks

4. **Embedding Generation** (⚠️ **Time-consuming**)
   - Generates 768-dimensional vectors for each chunk
   - **Batch size: 16 chunks** (optimized for memory)
   - **Progress logged every 100 batches**
   - For 55,512 chunks: ~3,470 batches = **20-40 minutes**

5. **Storage** (TODO - not yet implemented)
   - Stores chunks with embeddings in Couchbase
   - Creates indexes for fast retrieval

## Performance

### Expected Times

| Repo Size | Code Chunks | Time |
|-----------|-------------|------|
| Small (~1K files) | ~5,000 | 5-10 min |
| Medium (~10K files) | ~25,000 | 15-25 min |
| Large (~50K files) | ~55,000 | 30-60 min |

### Progress Tracking

Watch the logs to see progress:

```bash
docker-compose logs -f ingestion-worker | grep "Processing batch"
```

You'll see:
```
Processing batch 1/3470 (0.0%)
Processing batch 100/3470 (2.9%)
Processing batch 200/3470 (5.8%)
...
Processing batch 3400/3470 (98.0%)
✓ Generated embeddings for 55512 chunks
```

## Optimization Tips

### Memory Issues

If the worker crashes or hangs:

1. **Reduce batch size** in `ingestion-worker/embeddings/generator.py`:
   ```python
   batch_size: int = 8  # Default is 16
   ```

2. **Increase Docker memory** (Docker Desktop → Settings → Resources → Memory)

3. **Process fewer repos** at once - edit `.env`:
   ```bash
   GITHUB_REPOS=owner/repo1  # Process one at a time
   ```

### Speed Up Ingestion

1. **Use local clones** instead of GitHub API:
   ```bash
   ./clone-repos.sh owner/repo1 owner/repo2
   ```

2. **Skip git pull** if repos don't change often
   - Repos in `./repos/` are processed directly
   - Worker only pulls if repo already exists

3. **Run on machine with better CPU**
   - Embedding generation is CPU-intensive
   - M3 Ultra processes ~500 chunks/second
   - Regular machines: ~100-200 chunks/second

## Troubleshooting

### Worker Keeps Restarting

**Fixed!** The worker now has `restart: no` and uses the `manual` profile. It only runs when explicitly started with `./run-ingestion.sh`.

### Out of Memory (OOM)

**Symptoms**: Worker crashes silently during embedding generation

**Solutions**:
- Reduce batch size (see above)
- Increase Docker memory limit
- Process repos one at a time
- Close other applications to free RAM

### Slow Embedding Generation

**Normal behavior** for large repos. The embedding model (nomic-embed-text) processes each chunk through a neural network, which takes time.

**Progress should be steady**: ~100-500 chunks per minute depending on hardware.

If **completely stuck** (no progress for >5 minutes):
```bash
# Kill the worker
docker-compose stop ingestion-worker

# Check for errors
docker-compose logs ingestion-worker | tail -50

# Try with a smaller repo or reduced batch size
```

### "Repository not found" or Auth Errors

If using GitHub cloning:
1. Check `GITHUB_TOKEN` in `.env`
2. Verify token has `repo` access
3. Test manually: `git ls-remote https://github.com/owner/repo.git`

Or use local clones instead:
```bash
./clone-repos.sh owner/repo
```

## Advanced Usage

### Continuous Mode

To run ingestion continuously (e.g., every 24 hours):

1. Edit `docker-compose.yml`:
   ```yaml
   ingestion-worker:
     restart: unless-stopped
     profiles: []  # Remove manual profile
     environment:
       - RUN_MODE=continuous  # Default interval: 24 hours
   ```

2. Start normally:
   ```bash
   docker-compose up -d
   ```

### Custom Run Mode

Set environment variables before running:

```bash
# Process specific repos
GITHUB_REPOS=owner/repo1,owner/repo2 ./run-ingestion.sh

# Change log level
LOG_LEVEL=DEBUG ./run-ingestion.sh follow
```

### Batch Processing Multiple Repos

Create a script to process repos one at a time:

```bash
#!/bin/bash
for repo in "owner/repo1" "owner/repo2" "owner/repo3"; do
    echo "Processing $repo..."
    GITHUB_REPOS=$repo docker-compose --profile manual run --rm ingestion-worker
    echo "✓ Completed $repo"
    echo ""
done
```

## When to Run Ingestion

Run ingestion:
- **Initially** - Index all repositories
- **After code changes** - Re-index updated repos
- **Weekly/Monthly** - Keep embeddings fresh
- **On-demand** - When adding new repositories

## Next Steps

After ingestion completes:
1. Verify data in Couchbase UI: http://localhost:8091
2. Test search via MCP Server: http://localhost:8080/health
3. Connect Claude Desktop or MCP client
4. Start searching your codebase!

---

**Note**: The first run downloads and caches the embedding model (~2GB). Subsequent runs are faster.
