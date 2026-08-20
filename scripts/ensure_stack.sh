#!/bin/bash
# ensure_stack.sh — bring the code-smriti host stack to a live state, idempotently.
#
# Consumers:
#   1. com.codesmriti.ensure-stack  (system LaunchDaemon, RunAtLoad) — recovers the
#      stack at every boot with no GUI login required (headless Mac).
#   2. run_incremental.sh — preflight before the daily ingestion, so the job
#      self-heals even if boot-time recovery ever fails.
#
# Every step is a no-op when already satisfied, so this is safe to run repeatedly
# and from a periodic watchdog. It does NOT use `set -e`: a failure in one step
# (e.g. ollama) must not stop the others.

set -u

# --- Environment ------------------------------------------------------------
# A LaunchDaemon gives us almost no environment, so set it explicitly.
# colima/limactl/ollama all live in /opt/homebrew/bin.
export HOME="/Users/kaustubh"
export USER="kaustubh"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

LOG="$HOME/Documents/code/code-smriti/logs/ensure_stack.log"
LIMA_DIR="$HOME/.colima/_lima/colima"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') | $1" >> "$LOG"; }

log "=== ensure_stack start ==="

# --- 1. Colima / Docker -----------------------------------------------------
if colima status >/dev/null 2>&1; then
    log "colima: already running"
else
    log "colima: not running, starting"
    if ! colima start >>"$LOG" 2>&1; then
        # Most common cause is a stale VM after an unclean (OOM) shutdown. Force a
        # clean stop, drop pidfiles whose PID is provably dead, then retry once.
        log "colima: start failed, clearing stale state and retrying"
        colima stop --force >>"$LOG" 2>&1 || true
        for pidfile in "$LIMA_DIR/ha.pid" "$LIMA_DIR/vz.pid"; do
            if [[ -f "$pidfile" ]]; then
                pid="$(cat "$pidfile" 2>/dev/null)"
                if [[ -n "$pid" ]] && ! kill -0 "$pid" 2>/dev/null; then
                    log "colima: removing stale $pidfile (pid $pid is dead)"
                    rm -f "$pidfile"
                fi
            fi
        done
        if colima start >>"$LOG" 2>&1; then
            log "colima: started after retry"
        else
            log "colima: ERROR — start failed after retry (needs a human)"
        fi
    fi
    colima status >/dev/null 2>&1 && log "colima: running"
fi

# --- 2. Wait for Couchbase to answer ----------------------------------------
# Containers are `unless-stopped`, so they follow colima up — but the daily
# ingestion connects via the SDK and will fail if the node isn't accepting
# requests yet. Block (bounded) until the node responds.
log "couchbase: waiting for :8091"
couchbase_up=0
for i in $(seq 1 30); do
    # /pools answers 401 (auth required) when the node is healthy; only a
    # connection refusal returns "000". Any real HTTP status means it's up.
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:8091/pools" 2>/dev/null)"
    if [[ -n "$code" && "$code" != "000" ]]; then
        couchbase_up=1
        log "couchbase: healthy after $((i * 2))s (HTTP $code)"
        break
    fi
    sleep 2
done
[[ "$couchbase_up" -eq 0 ]] && log "couchbase: WARN — not healthy after 60s"

# --- 3. LM serving via ollama ----------------------------------------------
# ollama is the LLM provider for smriti + cos. We use the OFFICIAL macOS build
# (/Applications/Ollama.app), NOT the homebrew build: brew's source-build can
# ship without the llama-server (GGUF) runner, which silently breaks embeddings
# (nomic-embed-text) while MLX chat keeps working. The official bundle always
# ships llama-server + the MLX runners. Its com.ollama.server LaunchAgent is
# login-gated (Aqua), so on a headless / no-login boot we must start it here.
# Start only if nothing is already serving :11434 (so we don't clash with the
# LaunchAgent when a console session IS logged in). Bind 0.0.0.0 + allow any
# origin so Docker (host.docker.internal) and the public nginx /llm proxy reach
# it. Models load on demand (JIT) — no pre-loading, keeps resident RAM low.
OLLAMA_BIN="/Applications/Ollama.app/Contents/Resources/ollama"
if curl -sf -o /dev/null "http://localhost:11434/api/version" 2>/dev/null; then
    log "ollama: already serving :11434"
elif [[ ! -x "$OLLAMA_BIN" ]]; then
    log "ollama: WARN — official binary not found at $OLLAMA_BIN (install from ollama.com/download); NOT falling back to brew (it may lack llama-server)"
else
    log "ollama: not running, starting headless from official build"
    # The serving environment is READ FROM the com.ollama.server LaunchAgent
    # rather than duplicated here. These values are load-bearing for ingestion
    # throughput: OLLAMA_NUM_PARALLEL decides whether the GGUF chunker gets 4
    # slots or 1, and at 1 slot it serialises at ~20 tok/s and costs ~5x wall
    # clock. This path runs on a headless boot, where nothing would notice the
    # regression. A second hardcoded copy is exactly how the two drift: this
    # block previously pinned CONTEXT_LENGTH=65536 and omitted NUM_PARALLEL
    # entirely, silently undoing the tuning after any unattended reboot.
    OLLAMA_PLIST="$HOME/Library/LaunchAgents/com.ollama.server.plist"
    OLLAMA_ENV=()
    while IFS= read -r kv; do
        [[ -n "$kv" ]] && OLLAMA_ENV+=("$kv")
    done < <(/usr/bin/python3 - "$OLLAMA_PLIST" 2>/dev/null <<'PLIST_ENV'
import plistlib, sys
try:
    d = plistlib.load(open(sys.argv[1], 'rb'))
    for k, v in sorted((d.get('EnvironmentVariables') or {}).items()):
        if k.startswith('OLLAMA_') and ' ' not in str(v):
            print(f'{k}={v}')
except Exception:
    pass
PLIST_ENV
    )
    if [[ ${#OLLAMA_ENV[@]} -eq 0 ]]; then
        log "ollama: WARN - could not read env from $OLLAMA_PLIST; using fallback literals (keep in sync with the plist)"
        OLLAMA_ENV=(OLLAMA_HOST=0.0.0.0:11434 OLLAMA_FLASH_ATTENTION=1
                    OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_CONTEXT_LENGTH=16384
                    OLLAMA_NUM_PARALLEL=4 OLLAMA_KEEP_ALIVE=30m
                    OLLAMA_MAX_LOADED_MODELS=6)
    fi
    log "ollama: env ${OLLAMA_ENV[*]}"
    # OLLAMA_ORIGINS is not in the plist (the Aqua agent does not need it) but
    # Docker and the nginx /llm proxy do, so it is set only on this path.
    env "${OLLAMA_ENV[@]}" OLLAMA_ORIGINS='*' \
        nohup "$OLLAMA_BIN" serve >>"$LOG" 2>&1 &
    for i in $(seq 1 15); do
        curl -sf -o /dev/null "http://localhost:11434/api/version" 2>/dev/null \
            && { log "ollama: up"; break; }
        sleep 1
    done
fi

log "=== ensure_stack done ==="
