#!/bin/bash
#
# CodeSmriti Ingestion Worker Launcher
# Starts ingestion in a persistent tmux session with all required configuration
#

set -e  # Exit on error

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load GitHub token from root .env
if [ -f "../../.env" ]; then
    source ../../.env
else
    echo "❌ Error: ../../.env not found (need GITHUB_TOKEN)"
    exit 1
fi

if [ -z "$GITHUB_TOKEN" ]; then
    echo "❌ Error: GITHUB_TOKEN not set in ../../.env"
    exit 1
fi

# Default configuration (override with environment variables)
: ${GITHUB_REPOS:="kbhalerao/labcore,JessiePBhalerao/firstseedtests,kbhalerao/ask-kev-2026,kbhalerao/smartbarn2025,kbhalerao/508hCoverCrop"}
: ${EMBEDDING_BACKEND:="local"}
: ${COUCHBASE_HOST:="localhost"}
: ${COUCHBASE_USERNAME:="Administrator"}
: ${COUCHBASE_PASSWORD:="password123"}
: ${COUCHBASE_BUCKET:="code_kosha"}
: ${REPOS_PATH:="/tmp/repos"}
: ${LOG_LEVEL:="INFO"}
: ${TMUX_SESSION:="code-ingestion"}

# Generate log filename
LOG_FILE="/tmp/ingestion-$(date +%Y%m%d-%H%M%S).log"

# Check if tmux session already exists
if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    echo "⚠️  Warning: tmux session '$TMUX_SESSION' already exists"
    read -p "Kill existing session and restart? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        tmux kill-session -t "$TMUX_SESSION"
        echo "✓ Killed existing session"
    else
        echo "Aborting. To attach to existing session: tmux attach -t $TMUX_SESSION"
        exit 0
    fi
fi

# Verify venv has dependencies
if ! venv/bin/python3 -c "import loguru" 2>/dev/null; then
    echo "❌ Error: venv missing dependencies. Installing..."
    /opt/homebrew/bin/uv pip install --python venv/bin/python3 -r requirements.txt
    echo "✓ Dependencies installed"
fi

# Create tmux session and start ingestion
echo "Starting ingestion..."
echo "  Session: $TMUX_SESSION"
echo "  Repos: $GITHUB_REPOS"
echo "  Log: $LOG_FILE"
echo

tmux new-session -d -s "$TMUX_SESSION" \
    "GITHUB_TOKEN='$GITHUB_TOKEN' \
     GITHUB_REPOS='$GITHUB_REPOS' \
     EMBEDDING_BACKEND='$EMBEDDING_BACKEND' \
     COUCHBASE_HOST='$COUCHBASE_HOST' \
     COUCHBASE_USERNAME='$COUCHBASE_USERNAME' \
     COUCHBASE_PASSWORD='$COUCHBASE_PASSWORD' \
     COUCHBASE_BUCKET='$COUCHBASE_BUCKET' \
     REPOS_PATH='$REPOS_PATH' \
     LOG_LEVEL='$LOG_LEVEL' \
     venv/bin/python3 -u worker.py 2>&1 | tee $LOG_FILE"

echo "✓ Ingestion started in tmux session '$TMUX_SESSION'"
echo
echo "Monitor progress:"
echo "  tmux attach -t $TMUX_SESSION        # Attach to session"
echo "  tail -f $LOG_FILE                   # Watch log file"
echo "  tmux kill-session -t $TMUX_SESSION  # Stop ingestion"
echo
echo "Waiting 5 seconds for startup..."
sleep 5

# Show initial log output
echo "=== Initial Log Output ==="
tail -30 "$LOG_FILE"
