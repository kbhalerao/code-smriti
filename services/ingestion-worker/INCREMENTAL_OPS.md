# Incremental Ingestion Operations Guide

## Overview

The incremental ingestion system automatically updates the code index when repositories change.

**Key Features:**
- **File-based locking** - Prevents overlapping runs (even if one takes >24 hours)
- **Rotating logs** - `logs/incremental.log` with 10MB rotation, 30-day retention
- **Run history** - `ingestion_log` documents in Couchbase for monitoring
- **Graceful interruption** - Ctrl+C safely stops the run and records status

## Quick Commands

```bash
cd /Users/kaustubh/Documents/code/code-smriti/services/ingestion-worker

# Run incremental update
.venv/bin/python incremental_v4.py

# Check if already running
.venv/bin/python incremental_v4.py --status

# Dry run (preview changes)
.venv/bin/python incremental_v4.py --dry-run

# Single repo
.venv/bin/python incremental_v4.py --repo owner/name

# View logs
tail -f logs/incremental.log
tail -f logs/incremental.error.log
```

---

## Sanity Checks (Post-Ingestion)

Run these after a full ingestion or periodically to ensure data integrity:

### 1. Duplicate Detection
```sql
-- Files with multiple documents (should be 0)
SELECT repo_id, file_path, COUNT(*) as cnt
FROM `code_kosha`
WHERE type = 'file_index'
GROUP BY repo_id, file_path
HAVING COUNT(*) > 1
```

### 2. Orphaned Documents
```sql
-- Docs for repos no longer on disk (compare with filesystem)
SELECT DISTINCT repo_id FROM `code_kosha`
```

### 3. Missing Summaries
```sql
-- Repos without repo_summary
SELECT DISTINCT c.repo_id
FROM `code_kosha` c
WHERE c.type = 'file_index'
  AND c.repo_id NOT IN (
    SELECT repo_id FROM `code_kosha` WHERE type = 'repo_summary'
  )
```

### 4. Document Counts by Type
```sql
SELECT type, COUNT(*) as cnt
FROM `code_kosha`
GROUP BY type
ORDER BY cnt DESC
```

### 5. Embedding Coverage
```sql
-- Documents missing embeddings
SELECT type, COUNT(*) as missing_embeddings
FROM `code_kosha`
WHERE embedding IS MISSING OR embedding IS NULL
GROUP BY type
```

### 6. Recent Activity
```sql
-- Documents by repo (to verify recent ingestion)
SELECT repo_id, COUNT(*) as doc_count
FROM `code_kosha`
GROUP BY repo_id
ORDER BY doc_count DESC
```

---

## Periodic Scheduling (launchctl)

The incremental script has built-in overlap protection via file locking. If a scheduled run starts while another is still running, it will exit cleanly with a message.

### 1. Create the plist file

```bash
cat > ~/Library/LaunchAgents/com.codesmriti.incremental.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.codesmriti.incremental</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/kaustubh/Documents/code/code-smriti/services/ingestion-worker/.venv/bin/python</string>
        <string>/Users/kaustubh/Documents/code/code-smriti/services/ingestion-worker/incremental_v4.py</string>
        <string>--trigger</string>
        <string>scheduled</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/kaustubh/Documents/code/code-smriti/services/ingestion-worker</string>

    <key>StartCalendarInterval</key>
    <dict>
        <!-- Run at 3 PM daily (local time) -->
        <key>Hour</key>
        <integer>15</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <!-- Logs handled internally by loguru, but capture any startup errors -->
    <key>StandardErrorPath</key>
    <string>/Users/kaustubh/Documents/code/code-smriti/services/ingestion-worker/logs/launchd.error.log</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin</string>
    </dict>
</dict>
</plist>
EOF
```

### 2. Create logs directory
```bash
mkdir -p /Users/kaustubh/Documents/code/code-smriti/services/ingestion-worker/logs
```

### 3. Load the job
```bash
launchctl load ~/Library/LaunchAgents/com.codesmriti.incremental.plist
```

### 4. Useful commands
```bash
# Check if loaded
launchctl list | grep codesmriti

# Manually trigger a run
launchctl start com.codesmriti.incremental

# Check if ingestion is running
.venv/bin/python incremental_v4.py --status

# Stop/unload
launchctl unload ~/Library/LaunchAgents/com.codesmriti.incremental.plist

# View logs
tail -f logs/incremental.log
tail -f logs/incremental.error.log
```

---

## Run History (ingestion_log)

Every run creates an `ingestion_log` document in Couchbase:

```python
{
    "type": "ingestion_log",
    "document_id": "ingestion_log:20250102_060012_a1b2c3",
    "run_id": "20250102_060012_a1b2c3",
    "started_at": "2025-01-02T06:00:12.345Z",
    "completed_at": "2025-01-02T06:45:30.123Z",
    "status": "completed",  # completed, completed_with_errors, failed, interrupted
    "trigger": "scheduled",  # manual, scheduled, webhook
    "dry_run": false,
    "repos_processed": 169,
    "repos_skipped": 150,
    "repos_updated": 12,
    "repos_full_reingest": 7,
    "repos_cloned": 0,
    "repos_deleted": 0,
    "repos_error": 0,
    "files_processed": 234,
    "files_deleted": 5,
    "duration_seconds": 2718.5,
    "errors": []
}
```

### Query recent runs
```sql
SELECT run_id, started_at, status, duration_seconds, repos_updated, repos_error
FROM `code_kosha`
WHERE type = 'ingestion_log'
ORDER BY started_at DESC
LIMIT 10
```

### Check for failed runs in last 48 hours
```sql
SELECT run_id, started_at, status, errors
FROM `code_kosha`
WHERE type = 'ingestion_log'
  AND status IN ['failed', 'completed_with_errors']
  AND started_at > DATE_ADD_STR(NOW_STR(), -48, 'hour')
```

---

## Nice-to-Haves (Future)

### 1. Per-Document Timestamps
Add to all documents:
- `indexed_at`: timestamp of last indexing
- `commit_hash`: git commit when indexed

Query recently updated:
```sql
SELECT repo_id, file_path, indexed_at
FROM `code_kosha`
WHERE type = 'file_index'
ORDER BY indexed_at DESC
LIMIT 50
```

### 2. Simple Dashboard
- Total docs by type
- Last ingestion run time (query `ingestion_log`)
- Repos with recent changes
- Error count from last run

### 3. Alerting
- Slack/email notification on ingestion failures
- Alert if no successful run in 48 hours

### 4. Repo Sync Status View
```sql
-- Compare stored commit vs origin HEAD
SELECT repo_id, commit_hash as stored_commit
FROM `code_kosha`
WHERE type = 'repo_summary'
```
Then compare with `git ls-remote` for each repo.

---

## Quick Reference

```bash
cd /Users/kaustubh/Documents/code/code-smriti/services/ingestion-worker

# Check if running
.venv/bin/python incremental_v4.py --status

# Manual run
.venv/bin/python incremental_v4.py

# Single repo
.venv/bin/python incremental_v4.py --repo owner/name

# Dry run
.venv/bin/python incremental_v4.py --dry-run

# Logs
tail -f logs/incremental.log      # All activity
tail -f logs/incremental.error.log # Errors only
ls logs/run_*.log                  # Per-run logs
```
