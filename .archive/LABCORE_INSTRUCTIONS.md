# Running Labcore Ingestion (SSH-Resilient)

## Quick Start

Once the test/code-smriti ingestion completes, run labcore in a tmux session:

```bash
# Create tmux session
tmux new -s labcore-ingestion

# Inside tmux, run the script
./run-labcore-ingestion.sh

# Detach from tmux: Press Ctrl+B, then D
# This lets the ingestion continue even if SSH drops
```

## Monitoring Progress

```bash
# Reattach to see progress
tmux attach -t labcore-ingestion

# Or check the log file
tail -f logs/labcore-ingestion-*.log

# Or check Couchbase document count
curl -s -u Administrator:password123 "http://localhost:8093/query/service" \
  -d 'statement=SELECT COUNT(*) as count FROM `code_kosha`' | python3 -m json.tool
```

## What the Script Does

1. Updates `.env` to set `GITHUB_REPOS=kbhalerao/labcore`
2. Runs `./run-ingestion-native.sh` with full logging
3. Saves logs to `logs/labcore-ingestion-TIMESTAMP.log`
4. Survives SSH disconnections (when run in tmux)

## Expected Performance

For kbhalerao/labcore (~55K chunks, similar to code-smriti):
- **Parsing**: ~1 minute
- **Embeddings**: ~7-8 minutes (with local backend + MPS)
- **Database upsert**: ~1 minute
- **Total**: ~10 minutes

## If SSH Drops

```bash
# After reconnecting, check if tmux session still exists
tmux ls

# Reattach to the session
tmux attach -t labcore-ingestion

# Check the log file
ls -lh logs/labcore-ingestion-*.log
tail -100 logs/labcore-ingestion-*.log
```

## Manual Method (without tmux)

```bash
# Run with nohup if tmux isn't available
nohup ./run-labcore-ingestion.sh > /tmp/labcore-$(date +%s).log 2>&1 &

# Get the process ID
echo $!

# Monitor
tail -f /tmp/labcore-*.log
```

## Cleanup After Completion

```bash
# Exit tmux session (from inside tmux)
exit

# Or kill it from outside
tmux kill-session -t labcore-ingestion
```

## Current Configuration

- **Embedding Backend**: local (sentence-transformers + MPS)
- **Model**: all-mpnet-base-v2 (768 dimensions)
- **Incremental Updates**: DISABLED (first run processes all files)
- **Batch Size**: 128 chunks (embeddings), 100 chunks (Couchbase)
