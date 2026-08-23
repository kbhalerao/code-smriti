#!/bin/bash
# The ingestion tick: scan, drain, or both.
#
#   ./run_tick.sh drain     com.codesmriti.drain   every 5 minutes
#   ./run_tick.sh scan      com.codesmriti.scan    every 15 minutes
#   ./run_tick.sh           both, for a manual run
#
# Replaces the nightly batch as the thing that keeps the corpus current. Two
# processes, one after the other, each with its own lock and neither depending on
# the other's outcome:
#
#   scan   decides what needs doing and queues it. The only half that talks to
#          git. Its own lock, because ~100 fetches does not sit comfortably
#          inside five minutes and two overlapping scans would fight over
#          `.git` — `GitOperations.fetch` writes refspec config and moves
#          origin/HEAD.
#   drain  works the queue under the ingestion flock. Does no fetch at all:
#          every item is pinned to the commit range the scan decided against.
#
# If the scan queues something after the drain has already looked, it is picked
# up on the next tick. Five-minute eventual consistency is the whole protocol.
#
# **This script does not alert.** A tick that fires 288 times a day cannot page
# anyone on a transient failure without becoming noise, and noise is how an alert
# stops being read. Failures land in the queue's attempt counter and then the
# DLQ; run_daily.sh is what reports them, once, on a fingerprint.
#
# Both intervals sit at or under OLLAMA_KEEP_ALIVE (30m) so the ~29GB chunker
# weights stay resident between ticks instead of cold-loading on each one.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

LOG_FILE="$SCRIPT_DIR/logs/tick.log"
mkdir -p "$SCRIPT_DIR/logs"

# Essential environment for launchd
export HOME="${HOME:-$(eval echo ~$(whoami))}"
export USER="${USER:-$(whoami)}"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export LANG="en_US.UTF-8"

if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export PYTHONHOME=""
export PYTHONSTARTUP=""

# Disable MPS (Metal) to avoid GPU hangs in background processes
export PYTORCH_MPS_HIGH_WATERMARK_RATIO=0.0
export PYTORCH_ENABLE_MPS_FALLBACK=1
export CUDA_VISIBLE_DEVICES=""
export TOKENIZERS_PARALLELISM=false

# Step budgets. The drain's is far shorter than the old 10h run watchdog, because
# a kill now costs only the item in flight — the rest of the backlog is still in
# the queue when the next tick starts. Detecting a wedge went from half a day to
# this.
SCAN_TIMEOUT_SECS="${SCAN_TIMEOUT_SECS:-1200}"   # 20m
DRAIN_TIMEOUT_SECS="${DRAIN_TIMEOUT_SECS:-7200}" # 2h
TIMEOUT_RC=124

PYTHON="$SCRIPT_DIR/.venv/bin/python"

# Same watchdog as run_incremental.sh: macOS ships no timeout(1) and coreutils is
# not a dependency. Monitor mode puts the child in its own process group so the
# whole tree is signalled, not just the direct child.
run_with_timeout() {
    local limit=$1 label=$2
    shift 2
    local marker
    marker="$(mktemp "${TMPDIR:-/tmp}/codesmriti-tick-${label}.XXXXXX")"

    set -m
    "$@" >> "$LOG_FILE" 2>&1 &
    local child=$!
    set +m

    (
        local waited=0
        while (( waited < limit )); do
            kill -0 "$child" 2>/dev/null || exit 0
            sleep 10
            waited=$(( waited + 10 ))
        done
        kill -0 "$child" 2>/dev/null || exit 0
        echo fired > "$marker"
        echo "=== TIMEOUT: $label exceeded ${limit}s; terminating pid $child ===" >> "$LOG_FILE"
        kill -TERM -"$child" 2>/dev/null || kill -TERM "$child" 2>/dev/null
        sleep 30
        kill -KILL -"$child" 2>/dev/null || kill -KILL "$child" 2>/dev/null
    ) &
    local watchdog=$!

    local rc=0
    wait "$child" || rc=$?
    kill -KILL "$watchdog" 2>/dev/null || true
    wait "$watchdog" 2>/dev/null || true

    if [[ -s "$marker" ]]; then
        rm -f "$marker"
        return $TIMEOUT_RC
    fi
    rm -f "$marker"
    return $rc
}

# Which half to run. The two are on different cadences, but no longer because the
# scan is slow — parallelising it took 288 repos from 5m17s to ~46s. What is left
# is volume: a scan is 288 git fetches whatever its wall clock, so every five
# minutes would be ~3,400 an hour aimed at GitHub. Fifteen minutes is ~1,150 and
# still bounds push-to-indexed latency at a quarter hour, against the 24 hours the
# nightly batch allowed.
#
# The drain is the opposite — one indexed query when the queue is empty, ~2s — so
# it is the one that wants to be frequent.
STAGE="${1:-both}"

echo "=== $(date) tick ($STAGE) ===" >> "$LOG_FILE"
SCAN_RC=0
DRAIN_RC=0

# --- Scan -------------------------------------------------------------------
# Exits 0 and prints a skip line if another scan holds the lock, so an overrunning
# scan simply misses a tick rather than stacking.
if [[ "$STAGE" == "scan" || "$STAGE" == "both" ]]; then
    set +e
    run_with_timeout "$SCAN_TIMEOUT_SECS" scan "$PYTHON" -u "$SCRIPT_DIR/incremental_v4.py" --scan
    SCAN_RC=$?
    set -e
    [[ $SCAN_RC -ne 0 ]] && echo "scan exited $SCAN_RC" >> "$LOG_FILE"
fi

# --- Drain ------------------------------------------------------------------
# Exits 2 when the ingestion lock is held, which is the normal case whenever a
# drain is still working through the queue: this tick has nothing to add.
if [[ "$STAGE" == "drain" || "$STAGE" == "both" ]]; then
    set +e
    run_with_timeout "$DRAIN_TIMEOUT_SECS" drain "$PYTHON" -u "$SCRIPT_DIR/incremental_v4.py" --drain
    DRAIN_RC=$?
    set -e
fi

if [[ $DRAIN_RC -eq 2 ]]; then
    echo "drain skipped — a drain is already running" >> "$LOG_FILE"
elif [[ $DRAIN_RC -eq $TIMEOUT_RC ]]; then
    echo "drain hit its ${DRAIN_TIMEOUT_SECS}s budget and was killed; the queue survives" >> "$LOG_FILE"
elif [[ $DRAIN_RC -ne 0 ]]; then
    echo "drain exited $DRAIN_RC" >> "$LOG_FILE"
fi

echo "=== $(date) tick done (scan=$SCAN_RC drain=$DRAIN_RC) ===" >> "$LOG_FILE"
exit 0
