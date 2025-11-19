# CodeSmriti Session State - 2025-11-19

## Current Status
- **Ingestion process RUNNING** in background (PID: check with `ps aux | grep worker`)
- Output logging to: `/tmp/ingestion-run1.log`
- Started at: ~10:23 AM
- Currently stuck on: "Checking for file changes..." step (12K+ files to check)

## What's Been Done
1. ✅ Fixed local embedding generator model (changed to `all-mpnet-base-v2`)
2. ✅ Updated `.env`:
   - `EMBEDDING_BACKEND=local`
   - `GITHUB_REPOS=test/code-smriti`
3. ✅ Started first ingestion run
4. ✅ Successfully parsed 55,348 code chunks + 202 document chunks
5. ⏳ Waiting for file change detection to complete (slow, ~12K files)

## Next Steps

### If ingestion completes successfully:
1. Verify chunks in Couchbase:
   ```bash
   # Query Couchbase for test/code-smriti chunks
   # Check via UI at http://localhost:8091
   ```

2. Git commit all changes:
   ```bash
   git add .
   git status
   git commit -m "Add incremental updates and local embeddings"
   ```

3. Test incremental updates:
   ```bash
   # Run ingestion again - should skip unchanged files
   ./run-ingestion-native.sh
   # Expected: "55K files skipped (unchanged)"
   ```

4. Make a small change and test:
   ```bash
   echo "# Test change" >> README.md
   ./run-ingestion-native.sh
   # Expected: "1 updated, rest skipped"
   ```

### If SSH drops or need to check status:
```bash
# Check if process is running
ps aux | grep "python.*worker"

# Check latest logs
tail -f /tmp/ingestion-run1.log

# If process died, restart in tmux:
tmux new -s ingestion
./run-ingestion-native.sh 2>&1 | tee /tmp/ingestion-run2.log
# Ctrl+B then D to detach
```

### To optimize the slow file-change check:
The issue is in `worker.py:162-168` - sequential git + Couchbase queries per file.
Need to batch the Couchbase queries for better performance.

## Modified Files (uncommitted)
- `.env` - Added EMBEDDING_BACKEND=local, changed repo to test/code-smriti
- `ingestion-worker/embeddings/local_generator.py` - Changed to all-mpnet-base-v2
- `ingestion-worker/config.py` - Added embedding_backend option
- `ingestion-worker/worker.py` - Added incremental update logic
- `ingestion-worker/storage/couchbase_client.py` - Added file change methods
- `ingestion-worker/parsers/*.py` - Git metadata integration
- Plus others (see `git diff --stat`)

## Known Issues
1. **Slow file change detection** - Takes 10-20 min for large repos (12K+ files)
   - Solution: Batch Couchbase queries instead of one-by-one
2. **sentence-transformers model** - Using all-mpnet-base-v2 instead of nomic
   - Different embeddings than Ollama's nomic model
   - Need to fix if consistency is critical

## Performance Expectations
- **First run**: 10-15 minutes for 55K chunks (parsing + embedding + storage)
- **Incremental run**: <1 minute if no changes (just file checks + skip)
- **With 1-2% changes**: 1-2 minutes

## Environment
- macOS (Darwin 24.6.0)
- Python 3.9 (venv)
- Couchbase: localhost:8091 (Docker)
- MPS (Apple Silicon GPU) for embeddings
