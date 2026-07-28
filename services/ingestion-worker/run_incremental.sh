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

# Exit code used to signal "killed by the watchdog", matching GNU timeout(1).
TIMEOUT_RC=124

# Escape arbitrary text into a JSON string literal. Pure parameter expansion so
# the alert path stays free of python/jq — the thing we're alerting about may be
# a python that cannot start.
json_escape() {
    local s=$1
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\t'/\\t}
    s=${s//$'\r'/\\r}
    s=${s//$'\n'/\\n}
    printf '"%s"' "$s"
}

# Post a high-priority failure note straight to the Chief of Staff API via curl.
# Deliberately does not go through generate_daily_digest.py: when the failure is
# "the interpreter wedged", a python-based alert wedges too.
post_cos_alert() {
    local reason=$1 detail=$2

    if [[ -z "${COS_API_URL:-}" || -z "${COS_TOKEN:-}" ]]; then
        echo "COS_API_URL/COS_TOKEN unset; cannot post failure alert." >> "$LOG_FILE"
        return 0
    fi

    local content
    content="# ⚠️ Ingestion run failed — $(date '+%Y-%m-%d %H:%M %Z')

**${reason}**

${detail}

Host: $(hostname -s)
Log: \`services/ingestion-worker/logs/launchd.out.log\`

_The schedule has been released — the next run will start on time._"

    local payload
    payload="{\"doc_type\":\"note\",\"title\":$(json_escape "Ingestion FAILED — ${reason}"),\"content\":$(json_escape "$content"),\"tags\":[\"updates\",\"alert\"],\"status\":\"inbox\",\"priority\":\"high\",\"source\":{\"client\":\"code-smriti-ingestion\",\"project\":\"code-smriti\"}}"

    echo "Posting failure alert to cos..." >> "$LOG_FILE"
    curl -sS -m 30 -X POST "${COS_API_URL}/api/cos/docs" \
        -H "Authorization: Bearer ${COS_TOKEN}" \
        -H "Content-Type: application/json" \
        --data-binary "$payload" >> "$LOG_FILE" 2>&1 \
        || echo "Failure alert POST did not succeed." >> "$LOG_FILE"
    echo "" >> "$LOG_FILE"
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

if [[ $EXIT_CODE -eq $TIMEOUT_RC ]]; then
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
