# 3. Maintain

Keep your CodeSmriti knowledge base up-to-date.

## Quick Commands

```bash
# Monitor active pipeline
./monitor

# Run incremental updates
./update-repos

# Check system health
./health-check

# View statistics
./stats

# Backup database
./backup
```

## Scripts

### monitor

Watch the active ingestion pipeline in real-time.

Shows:
- Current repository being processed
- Progress (repo X/Y)
- Chunks processed
- Errors/warnings

### update-repos

Run incremental updates for changed repositories.

**What it does:**
1. Checks each repo for new commits
2. Only processes changed files (file-level granularity)
3. Atomically deletes old chunks for changed files
4. Generates and stores new chunks

**When to run:**
- Daily (via cron): Catch recent changes
- Weekly: For less active repos
- After major updates: Manual trigger

**Cron example:**
```bash
# Daily at 3 AM
0 3 * * * cd /path/to/code-smriti/3-maintain && ./update-repos
```

### health-check

Verifies system health:
- Couchbase running
- Database accessible
- Vector index operational
- Disk space available
- Memory usage OK

**Exit codes:**
- 0: All healthy
- 1: Warning (degraded)
- 2: Error (down)

### stats

Shows knowledge base statistics:
```
Total repositories: 97
Total chunks: 50,234
Code chunks: 45,112
Documents: 3,890
Commits: 1,232

By repository:
  owner/repo1: 15,234 chunks
  owner/repo2: 8,901 chunks
  ...

Languages indexed:
  Python: 65%
  JavaScript: 25%
  TypeScript: 10%
```

### backup

Backs up Couchbase database to local disk.

**Location:** `/backups/code_kosha_YYYYMMDD.tar.gz`

## Monitoring

### Log Files

Logs are written to:
```
/tmp/pipeline-ingestion-YYYYMMDD-HHMMSS.log
```

**Useful commands:**
```bash
# Watch live
tail -f /tmp/pipeline-ingestion-*.log

# Check errors
grep ERROR /tmp/pipeline-ingestion-*.log

# Monitor progress
grep "Processing batch" /tmp/pipeline-ingestion-*.log
```

### Database Stats

```bash
# Quick count
curl -s -u Administrator:password123 \
  http://localhost:8091/pools/default/buckets/code_kosha/stats | \
  python3 -c "import sys, json; data=json.load(sys.stdin); print(f\"Chunks: {int(data['op']['samples']['curr_items'][-1])}\")"
```

## Incremental Updates

CodeSmriti uses **file-level incremental updates**:

1. **Git detection:** Checks commit hash per file
2. **Changed files only:** Only re-processes modified files
3. **Atomic update:** Deletes all old chunks for file → re-parses → stores new chunks
4. **Fast:** ~1 minute for typical update vs 15 minutes for full re-ingestion

**Example:**
```
Repository: kbhalerao/labcore
Files checked: 234
Changed files: 3
Old chunks deleted: 47
New chunks stored: 52
Time: 45 seconds
```

## Next Step

→ **[4-consume](../4-consume/README.md)** - Start using your knowledge base
