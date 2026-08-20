#!/bin/bash
# Wrapper script for scheduled incremental ingestion
# Called by launchd (com.codesmriti.incremental)
#
# Every step runs under a hard wall-clock timeout. This is not belt-and-braces:
# launchd will not start a second instance of a StartCalendarInterval job while
# the previous one is still alive, so a single wedged run silently suppresses
# every subsequent day's run — and `launchctl list` keeps reporting the last
# *completed* exit code, so the job still looks healthy. On 2026-07-24 the
# ingestion python wedged inside interpreter startup and cost four days of runs
# before anyone noticed. Bounding each step guarantees the schedule recovers.

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

# Step timeouts (seconds). Ingestion legitimately runs long — the slowest
# observed full pass was ~6h45m — so the cap only has to beat the 24h gap to the
# next scheduled run, not the typical runtime. Override in .env if needed.
INGEST_TIMEOUT_SECS="${INGEST_TIMEOUT_SECS:-36000}"   # 10h
KPI_TIMEOUT_SECS="${KPI_TIMEOUT_SECS:-900}"           # 15m
DIGEST_TIMEOUT_SECS="${DIGEST_TIMEOUT_SECS:-1800}"    # 30m
ALERT_TIMEOUT_SECS="${ALERT_TIMEOUT_SECS:-120}"       # 2m

# Exit code used to signal "killed by the watchdog", matching GNU timeout(1).
TIMEOUT_RC=124

# incremental_v4.py exits 2 when it cannot take the run lock because another
# ingestion is already live (v4/incremental/runner.py raises LockError). That is
# the lock doing its job, not a failure: a long manual run legitimately overlaps
# the schedule. Treated as a clean skip so it neither pages anyone nor refreshes
# the dashboard and digest off a run that never happened.
LOCK_BUSY_RC=2

# The cos CLI lives in ~/.local/bin, which is not on the minimal PATH launchd
# hands us, so it has to be added explicitly. Resolved once, up front, so a
# missing CLI is reported rather than discovered mid-failure.
export PATH="$PATH:$HOME/.local/bin"
COS_BIN="$(command -v cos || true)"
COS_DIGEST_PROJECT_ID="${COS_DIGEST_PROJECT_ID:-7e3aaaab-5b4c-43d9-ac52-2bcb88c8bd49}"

# Post a high-priority failure note to the Chief of Staff inbox via the cos CLI,
# which owns the endpoint, schema and its own credentials (~/.config/cos/env) —
# so this script never hand-rolls a payload or carries a second copy of the
# token. cos runs on its own uv tool interpreter, independent of this repo's
# venv, so a broken ingestion venv does not take the alert path down with it.
#
# Bounded by the watchdog like every other step: an alert that hangs would
# recreate exactly the failure it exists to report.
post_cos_alert() {
    local reason=$1 detail=$2

    if [[ -z "$COS_BIN" ]]; then
        echo "cos CLI not found on PATH; cannot post failure alert." >> "$LOG_FILE"
        return 0
    fi

    local content
    content="# ⚠️ Ingestion run failed — $(date '+%Y-%m-%d %H:%M %Z')

**${reason}**

${detail}

Host: $(hostname -s)
Log: \`services/ingestion-worker/logs/launchd.out.log\`

_The schedule has been released — the next run will start on time._"

    echo "Posting failure alert to cos..." >> "$LOG_FILE"
    set +e
    run_with_timeout "$ALERT_TIMEOUT_SECS" alert \
        "$COS_BIN" doc create "$content" \
            --type note \
            --status inbox \
            --priority high \
            --tag updates \
            --tag alert \
            --project "$COS_DIGEST_PROJECT_ID"
    local alert_rc=$?
    set -e
    if [[ $alert_rc -ne 0 ]]; then
        echo "Failure alert did not post (cos exit $alert_rc)." >> "$LOG_FILE"
    fi
}

# Run a command with a hard wall-clock timeout, appending its output to LOG_FILE.
# macOS ships no timeout(1) and coreutils is not a dependency here, so this is
# done with a watchdog subshell.
#
# Returns the command's exit code, or $TIMEOUT_RC if the watchdog had to kill it.
run_with_timeout() {
    local limit=$1 label=$2
    shift 2

    # The watchdog runs in a subshell and so cannot set a variable in this
    # scope; it reports "I fired" by writing to this file. Created up front via
    # mktemp (empty == did not fire) so the watchdog only ever writes to a file
    # that already exists and is known writable — if the marker write failed we
    # would misreport a timeout as an ordinary signal exit.
    local marker
    marker="$(mktemp "${TMPDIR:-/tmp}/codesmriti-timeout-${label}.XXXXXX")"

    # Monitor mode puts each background job in its own process group, so the
    # watchdog can signal the whole tree (python + any subprocesses) rather than
    # just the direct child. Orphaned grandchildren would otherwise re-parent to
    # launchd and keep the job looking alive — the exact trap we're escaping.
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

PYTHON="$SCRIPT_DIR/.venv/bin/python"
echo "Using: $PYTHON" >> "$LOG_FILE"

# --- Ingestion -------------------------------------------------------------
set +e
run_with_timeout "$INGEST_TIMEOUT_SECS" ingest \
    "$PYTHON" -u "$SCRIPT_DIR/incremental_v4.py" "$@"
EXIT_CODE=$?
set -e

if [[ $EXIT_CODE -eq $LOCK_BUSY_RC ]]; then
    echo "Another ingestion holds the run lock; skipping this scheduled run." >> "$LOG_FILE"
    echo "=== $(date) Skipped (lock held by a live run) ===" >> "$LOG_FILE"
    exit 0
elif [[ $EXIT_CODE -eq $TIMEOUT_RC ]]; then
    echo "Ingestion timed out after ${INGEST_TIMEOUT_SECS}s and was killed." >> "$LOG_FILE"
    post_cos_alert \
        "ingestion timed out after ${INGEST_TIMEOUT_SECS}s" \
        "The run was killed by the watchdog so it could not suppress tomorrow's scheduled run. No digest was posted for today because the ingestion never completed."
elif [[ $EXIT_CODE -ne 0 ]]; then
    echo "Ingestion failed (exit $EXIT_CODE)." >> "$LOG_FILE"
    post_cos_alert \
        "ingestion exited $EXIT_CODE" \
        "The ingestion step failed. No digest was posted for today because there is no fresh run to summarize."
fi

# --- KPI dashboard ---------------------------------------------------------
# Only meaningful off a completed ingestion; regenerating after a failed run
# would refresh the page timestamp while the underlying data stayed stale.
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "Regenerating KPI dashboard..." >> "$LOG_FILE"
    set +e
    run_with_timeout "$KPI_TIMEOUT_SECS" kpi \
        "$PYTHON" -u "$SCRIPT_DIR/scripts/generate_kpi.py"
    KPI_EXIT=$?
    set -e

    if [[ $KPI_EXIT -ne 0 ]]; then
        echo "KPI generation failed (exit $KPI_EXIT)" >> "$LOG_FILE"
        EXIT_CODE=$KPI_EXIT
        post_cos_alert \
            "KPI dashboard generation exited $KPI_EXIT" \
            "Ingestion succeeded but the KPI dashboard was not regenerated, so landing/kpi.html is stale."
    fi
else
    echo "Skipping KPI regeneration — ingestion did not succeed." >> "$LOG_FILE"
fi

# --- Daily digest ----------------------------------------------------------
# Also gated on a successful ingestion: the digest summarizes "the most recent
# ingestion_run doc", which after a failed run is yesterday's. Posting that
# would read as a fresh digest and mask the outage.
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "Posting daily digest..." >> "$LOG_FILE"
    set +e
    run_with_timeout "$DIGEST_TIMEOUT_SECS" digest \
        "$PYTHON" -u "$SCRIPT_DIR/scripts/generate_daily_digest.py"
    DIGEST_EXIT=$?
    set -e
    if [[ $DIGEST_EXIT -ne 0 ]]; then
        # Fail-soft on the run's exit code, but say so out loud — a missing
        # digest is the symptom the user actually notices.
        echo "Daily digest step exited non-zero ($DIGEST_EXIT); continuing." >> "$LOG_FILE"
        post_cos_alert \
            "daily digest step exited $DIGEST_EXIT" \
            "Ingestion and KPI generation succeeded, but the digest could not be generated or posted."
    fi
else
    echo "Skipping daily digest — ingestion did not succeed." >> "$LOG_FILE"
fi

echo "=== $(date) Finished with exit code $EXIT_CODE ===" >> "$LOG_FILE"
exit $EXIT_CODE
