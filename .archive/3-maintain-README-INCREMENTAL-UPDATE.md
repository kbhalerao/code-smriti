# Incremental Update System

Automatically processes new repositories and updates existing ones with the latest changes.

## What It Does

The incremental update script (`incremental_update.py`) performs two operations:

1. **New Repository Ingestion**: Finds repos in `1-config/repos_to_ingest.txt` that aren't in the database yet and performs full ingestion
2. **Incremental Updates**: For repos already in the database, pulls latest changes and only processes modified files (using content-hash based change detection)

## Usage

### Manual Run

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

### Automated Scheduling (Cron)

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

### macOS LaunchAgent (Recommended for Mac)

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

## How Incremental Updates Work

1. **File Change Detection**: Uses content-based hashing to detect which files have changed
2. **Selective Re-processing**: Only re-parses and re-embeds chunks from modified files
3. **Chunk Updates**: Updates chunks in-place using the same chunk IDs (based on file path + position)
4. **Commit Tracking**: Updates git metadata (commit hash, author, date) for modified files

## Configuration

The script automatically uses these settings:
- `ENABLE_INCREMENTAL_UPDATES=true` - Enables incremental processing
- `REPOS_PATH=/Users/kaustubh/Documents/codesmriti-repos` - Repository storage location
- Reads from `1-config/repos_to_ingest.txt`
- Connects to Couchbase at `localhost:8091`

## Output

The script provides detailed logging:
- **Phase 1**: Full ingestion of new repos
- **Phase 2**: Incremental updates of existing repos
- Summary statistics (chunks added/updated, time elapsed)
- Lists any failed repositories

## Exit Codes

- `0`: Success
- `1`: One or more repositories failed to process

## Testing

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

## Monitoring

Check recent logs:
```bash
# View latest incremental update log
ls -lt /tmp/incremental-update-*.log | head -1 | xargs tail -50

# Monitor active run
tail -f /tmp/incremental-update-$(date +%Y%m%d)*.log
```

## Troubleshooting

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
