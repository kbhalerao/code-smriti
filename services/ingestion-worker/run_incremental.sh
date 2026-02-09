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
export HOME="${HOME:-$(eval echo ~$(whoami))}"
export USER="${USER:-$(whoami)}"
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

# Run incremental ingestion (capture exit code, don't bail on failure)
set +e
$PYTHON -u "$SCRIPT_DIR/incremental_v4.py" "$@" >> "$LOG_FILE" 2>&1
EXIT_CODE=$?
set -e

# Regenerate KPI dashboard after ingestion
echo "Regenerating KPI dashboard..." >> "$LOG_FILE"
set +e
$PYTHON -u "$SCRIPT_DIR/scripts/generate_kpi.py" >> "$LOG_FILE" 2>&1
KPI_EXIT=$?
set -e

if [[ $KPI_EXIT -ne 0 ]]; then
    echo "KPI generation failed (exit $KPI_EXIT)" >> "$LOG_FILE"
    # Propagate KPI failure if ingestion itself succeeded
    [[ $EXIT_CODE -eq 0 ]] && EXIT_CODE=$KPI_EXIT
fi

echo "=== $(date) Finished with exit code $EXIT_CODE ===" >> "$LOG_FILE"
exit $EXIT_CODE
