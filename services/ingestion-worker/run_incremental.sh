#!/bin/bash
# Wrapper script for scheduled incremental ingestion
# Called by launchd (com.codesmriti.incremental)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Logging - append to log file
LOG_FILE="$SCRIPT_DIR/logs/launchd.out.log"
echo "=== $(date) Starting incremental ingestion ===" >> "$LOG_FILE"

# Essential environment for launchd
export HOME="/Users/kaustubh"
export USER="kaustubh"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export LANG="en_US.UTF-8"

# Load environment variables from .env
if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

# Python non-interactive mode
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHOME=""
export PYTHONSTARTUP=""

# Disable MPS (Metal) to avoid GPU hangs in background processes
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0
export PYTORCH_ENABLE_MPS_FALLBACK=1
export CUDA_VISIBLE_DEVICES=""
export TOKENIZERS_PARALLELISM=false

# Use venv Python directly
PYTHON="$SCRIPT_DIR/.venv/bin/python"

echo "Using: $PYTHON" >> "$LOG_FILE"

# Run incremental ingestion
$PYTHON -u "$SCRIPT_DIR/incremental_v4.py" "$@" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?

echo "=== $(date) Finished with exit code $EXIT_CODE ===" >> "$LOG_FILE"
exit $EXIT_CODE
