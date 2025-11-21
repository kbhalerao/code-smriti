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

The incremental update system automatically processes new repositories and updates existing ones with the latest changes.

### What It Does

The incremental update script (`incremental_update.py`) performs two operations:

1. **New Repository Ingestion**: Finds repos in `1-config/repos_to_ingest.txt` that aren't in the database yet and performs full ingestion
2. **Incremental Updates**: For repos already in the database, pulls latest changes and only processes modified files (using content-hash based change detection)

### Usage

#### Manual Run

```bash
# From project root
./3-maintain/run-incremental-update
```

The script will:
- Query the database for existing repos
- Read `1-config/repos_to_ingest.txt`
- Categorize repos as "new" or "to update"
- Process new repos with full ingestion
- Update existing repos with incremental changes only
- Log everything to `/tmp/incremental-update-YYYYMMDD-HHMMSS.log`

#### Automated Scheduling (Cron)

To run daily at 2 AM:

```bash
# Edit crontab
crontab -e

# Add this line:
0 2 * * * /Users/kaustubh/Documents/code/code-smriti/3-maintain/run-incremental-update >> /tmp/cron-incremental-update.log 2>&1
```

To run every 6 hours:
```bash
0 */6 * * * /Users/kaustubh/Documents/code/code-smriti/3-maintain/run-incremental-update >> /tmp/cron-incremental-update.log 2>&1
```

#### macOS LaunchAgent (Recommended for Mac)

For more reliable scheduling on macOS, create a LaunchAgent:

```bash
# Create plist file
cat > ~/Library/LaunchAgents/com.codesmriti.incremental-update.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.codesmriti.incremental-update</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/kaustubh/Documents/code/code-smriti/3-maintain/run-incremental-update</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>2</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/incremental-update-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/incremental-update-stderr.log</string>
</dict>
</plist>
EOF

# Load the LaunchAgent
launchctl load ~/Library/LaunchAgents/com.codesmriti.incremental-update.plist

# To unload (disable)
launchctl unload ~/Library/LaunchAgents/com.codesmriti.incremental-update.plist
```

### How It Works

CodeSmriti uses **file-level incremental updates**:

1. **File Change Detection**: Uses content-based hashing to detect which files have changed
2. **Selective Re-processing**: Only re-parses and re-embeds chunks from modified files
3. **Chunk Updates**: Updates chunks in-place using the same chunk IDs (based on file path + position)
4. **Commit Tracking**: Updates git metadata (commit hash, author, date) for modified files
5. **Fast**: ~1 minute for typical update vs 15 minutes for full re-ingestion

**Example:**
```
Repository: kbhalerao/labcore
Files checked: 234
Changed files: 3
Old chunks deleted: 47
New chunks stored: 52
Time: 45 seconds
```

### Configuration

The script automatically uses these settings:
- `ENABLE_INCREMENTAL_UPDATES=true` - Enables incremental processing
- `REPOS_PATH=/Users/kaustubh/Documents/codesmriti-repos` - Repository storage location
- Reads from `1-config/repos_to_ingest.txt`
- Connects to Couchbase at `localhost:8091`

### Output

The script provides detailed logging:
- **Phase 1**: Full ingestion of new repos
- **Phase 2**: Incremental updates of existing repos
- Summary statistics (chunks added/updated, time elapsed)
- Lists any failed repositories

### Exit Codes

- `0`: Success
- `1`: One or more repositories failed to process

### Testing

To verify incremental updates work correctly, you can:

1. Make changes to a repository (e.g., edit a README)
2. Run the incremental update script
3. Check the logs - should show only the modified files being processed

Example test:
```bash
# Edit a file in an existing repo
echo "# Test change" >> ~/Documents/codesmriti-repos/kbhalerao_code-smriti/README.md

# Commit the change
cd ~/Documents/codesmriti-repos/kbhalerao_code-smriti
git add . && git commit -m "test incremental update"

# Run incremental update
./3-maintain/run-incremental-update

# Check logs - should show only README.md was re-processed
```

### Monitoring Incremental Updates

Check recent logs:
```bash
# View latest incremental update log
ls -lt /tmp/incremental-update-*.log | head -1 | xargs tail -50

# Monitor active run
tail -f /tmp/incremental-update-$(date +%Y%m%d)*.log
```

### Troubleshooting

**No changes detected?**
- Verify the repository has new commits: `cd ~/Documents/codesmriti-repos/<repo> && git log -1`
- Check if files actually changed: `git diff HEAD~1`

**Updates taking too long?**
- Large repos with many changes will take longer
- Consider running updates during off-peak hours

**Script fails to start?**
- Check virtual environment exists: `lib/ingestion-worker/venv/bin/python3`
- Verify Couchbase is running: `docker ps | grep couchbase`
- Check database credentials in `.env` file

## Next Step

→ **[4-consume](../4-consume/README.md)** - Start using your knowledge base
