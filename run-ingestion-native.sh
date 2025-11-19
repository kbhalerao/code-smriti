#!/bin/bash
# Run ingestion worker natively (outside Docker)
# This uses all available system RAM and exits when done

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKER_DIR="$SCRIPT_DIR/ingestion-worker"

# Load .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)
fi

# Set defaults if not in .env
export COUCHBASE_HOST="${COUCHBASE_HOST:-localhost}"
export COUCHBASE_PORT="${COUCHBASE_PORT:-8091}"
export COUCHBASE_USERNAME="${COUCHBASE_USERNAME:-Administrator}"
export COUCHBASE_BUCKET="${COUCHBASE_BUCKET:-code_memory}"
export EMBEDDING_MODEL="${EMBEDDING_MODEL:-nomic-ai/nomic-embed-text-v1.5}"
# Note: Model is cached locally after first download, won't re-download
export LOG_LEVEL="${LOG_LEVEL:-INFO}"

# Set repos path for native execution (Docker uses /repos)
export REPOS_PATH="$SCRIPT_DIR/repos"

# Validate required variables
if [ -z "$COUCHBASE_PASSWORD" ]; then
    echo "❌ Error: COUCHBASE_PASSWORD not set in .env"
    exit 1
fi

if [ -z "$GITHUB_REPOS" ]; then
    echo "❌ Error: GITHUB_REPOS not set in .env"
    exit 1
fi

echo "╔════════════════════════════════════════════════════════════╗"
echo "║       CodeSmriti Native Ingestion Worker                  ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "Configuration:"
echo "  • Couchbase: $COUCHBASE_HOST:$COUCHBASE_PORT"
echo "  • Repositories: $GITHUB_REPOS"
echo "  • Repos Path: $REPOS_PATH"
echo "  • Embedding Model: $EMBEDDING_MODEL"
echo "  • Log Level: $LOG_LEVEL"
echo ""

# Check if venv exists
if [ ! -d "$WORKER_DIR/venv" ]; then
    echo "❌ Virtual environment not found. Run setup first:"
    echo "   cd ingestion-worker && python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi

# Activate virtual environment
source "$WORKER_DIR/venv/bin/activate"

# Check if dependencies are installed
if ! python -c "import sentence_transformers" 2>/dev/null; then
    echo "❌ Dependencies not installed. Installing now..."
    pip install -q -r "$WORKER_DIR/requirements.txt"
    echo "✓ Dependencies installed"
fi

echo "Starting ingestion..."
echo "════════════════════════════════════════════════════════════"
echo ""

# Run the worker
cd "$WORKER_DIR"
python worker.py

echo ""
echo "════════════════════════════════════════════════════════════"
echo "✓ Ingestion complete!"
echo ""

# Show memory usage
echo "System memory after completion:"
vm_stat | grep "Pages free" || free -h 2>/dev/null || echo "(memory stats not available)"
