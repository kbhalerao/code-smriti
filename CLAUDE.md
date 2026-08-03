# Claude Code Project Instructions

## Python Environment

This project uses **uv** for Python package management. Always use `uv run` to execute Python scripts:

```bash
# Correct way to run Python scripts
uv run python script.py

# Run from specific service directory
cd services/ingestion-worker && uv run python -c "..."
```

Do NOT use:
- `python` directly (may use wrong environment)
- `source venv/bin/activate` or `source .venv/bin/activate`
- `pip install` (use `uv add` or `uv pip install` instead)

## Couchbase Database

Credentials are in `.env` at project root. The bucket is `code_kosha` (not `code-smriti`).

## Embedding Pipeline

All embeddings must be **normalized to unit length** (L2 norm = 1.0) for the FTS vector index which uses `dot_product` similarity.

Key files:
- `services/ingestion-worker/embeddings/local_generator.py` - Core embedding generation
- `services/api-server/app/rag/tools.py` - Search query embeddings

Use `search_document:` prefix for indexed documents and `search_query:` prefix for search queries.

## Couchbase FTS Testing

When testing FTS queries from the command line, use Python to handle authentication (the password has special characters that break shell escaping):

```bash
cd services/ingestion-worker && uv run python -c "
import httpx
import os
from dotenv import load_dotenv

load_dotenv('../../.env')  # relative to services/ingestion-worker
password = os.environ['COUCHBASE_PASSWORD']

resp = httpx.post(
    'http://localhost:8094/api/index/code_vector_index/query',
    auth=('Administrator', password),
    json={
        'query': {'term': 'repo_bdr', 'field': 'type'},
        'fields': ['content', 'repo_id'],
        'size': 3
    }
)
for hit in resp.json().get('hits', []):
    print(hit.get('fields', {}))
"
```

Key endpoints:
- **FTS queries**: `http://localhost:8094/api/index/{index_name}/query`
- **Index count**: `http://localhost:8094/api/index/{index_name}/count`
- **Index list**: `http://localhost:8094/api/index`

Auth is always `Administrator` + `COUCHBASE_PASSWORD` from `.env`.

See `docs/FTS_VECTOR_SEARCH.md` for hybrid search strategies and troubleshooting.

## Local LLM Setup (LM Studio)

LM Studio provides local LLM inference on port 1234, proxied via nginx at `/llm/*`.

### Auto-start Configuration

**LaunchAgent**: `~/Library/LaunchAgents/com.lmstudio.server.plist`
**Startup script**: `~/.lmstudio/startup.sh`

The startup script:
1. Starts LM Studio server with `--bind 0.0.0.0 --cors`
2. Loads models with specified context lengths (skips if already loaded)

### Models & Context Lengths

| Model | Context | Size |
|-------|---------|------|
| qwen/qwen3-30b-a3b-2507 | 128K | 17 GB |
| qwen/qwen3-next-80b | 128K | 45 GB |
| ibm/granite-4-h-tiny | 16K | 4 GB |
| text-embedding-nomic-embed-text-v1.5 | default | 84 MB |

### Troubleshooting After Power Failure

If LLM proxy returns 502:
1. Check LM Studio is running: `lms status`
2. Start if needed: `lms server start --port 1234 --bind 0.0.0.0 --cors`
3. Load models via GUI (CLI `lms load` may fail if LM Studio app isn't open)
4. If Colima's `host.docker.internal` is stale, restart Colima: `colima stop && colima start`

### Manual Commands

```bash
# Check status
lms status

# Start server
lms server start --port 1234 --bind 0.0.0.0 --cors

# Load model with context
lms load qwen/qwen3-30b-a3b-2507 --context-length 131072 --yes

# Test from host
curl http://localhost:1234/v1/models

# Test via nginx proxy.
# /llm/* is restricted to LAN/VLAN sources (the `geo $llm_allowed` block in
# services/api-gateway/nginx.conf). Requests from the host arrive NAT'd through
# the Docker bridge as 172.28.0.1, which is deliberately NOT allowlisted, so a
# bare localhost call returns 403. Present a LAN address the way the edge proxy
# does to exercise the real path:
curl -H 'X-Forwarded-For: 192.168.11.29' http://localhost/llm/v1/models
```

### Off-LAN callers on `/llm`

Ollama has no authentication of its own, so the gateway is the only access
control on the inference endpoints. A caller passes if it is **on a trusted
network OR presents the shared secret** — `$llm_pass`, the combination of the
`geo $llm_allowed` and `map $llm_key_ok` blocks. The LAN needs no key, so local
tooling and the curl recipes above are unaffected.

Off-LAN callers should use the **shared secret**, not an IP entry:

```bash
curl -H 'X-Smriti-Key: <secret>' https://smriti.agsci.com/llm/v1/models
```

The secret lives in `services/api-gateway/llm-allowlist.d/llm-key.conf`
(gitignored, mode 600; copy `llm-key.conf.example`). Rotation is zero-downtime:
add the new line, deploy, move callers over, delete the old line — every listed
value is accepted while present.

The IP allowlist in `allowlist.conf` still works and is currently carrying the
ListingsAISearch backend, but it is the **legacy path**. It is brittle: it
breaks silently if an egress address moves, and it puts third-party production
IPs a `git add` away from a public repo. Remove entries once the caller sends a
key.

Both files are bind-mounted as a *directory* at `/etc/nginx/llm-allowlist.d/`,
which sidesteps the stale-inode problem single-file bind mounts have under
Colima. Editing either still needs `docker-compose restart api-gateway`.

Four traps:

- The filenames in both `include`s are **fixed, not globs**. `include` inside a
  `geo` or `map` block is handled by that module's own parser, which opens the
  path literally and does not expand wildcards — `*.conf` fails startup with
  `open() ".../*.conf" failed (2: No such file or directory)`.
- A **missing** `allowlist.conf` or `llm-key.conf` is fatal to nginx startup,
  deliberately. It takes every vhost on this gateway down, not just `/llm`, so
  check `docker logs codesmriti_nginx` for `[emerg]` after any recreate.
- `map_hash_bucket_size 128` is load-bearing. A 64-char hex secret plus map
  overhead exceeds the 64-byte default and nginx refuses to start with
  `could not build map_hash`. Raise it (powers of two) before lengthening the
  secret.
- Never add `$http_x_smriti_key` to `log_format`. It would write the secret
  into a log with a far wider readership than the config. The gateway also
  strips the header before proxying upstream, so Ollama never sees it.

## Scheduled Ingestion (LaunchAgents)

Code ingestion runs automatically via macOS LaunchAgents. Both must remain loaded for the system to stay current.

### LaunchAgents

| Agent | Schedule | Purpose |
|-------|----------|---------|
| `com.codesmriti.incremental` | Daily 15:05 | Incremental repo sync + KPI dashboard regeneration |
| `com.codesmriti.bdr` | Weekly Sun 16:00 | BDR (Business Development Records) enrichment |

**Plist files**: `~/Library/LaunchAgents/com.codesmriti.*.plist`

### Checking Status

```bash
# List loaded agents
launchctl list | grep codesmriti

# Exit code 0 = last run succeeded, non-zero = failed
# "-" in PID column = not currently running
```

**The exit code alone does not mean the job is healthy.** It reports the last
*completed* run, so a currently-wedged run leaves a stale `0` sitting there
indefinitely. Read the PID column first: a number means an instance is running
right now, and launchd will **not** start a second instance of a
`StartCalendarInterval` job while one is alive — so a single hung run silently
suppresses every subsequent day. Check how long it has been running:

```bash
# A daily job whose elapsed time is measured in days is wedged, not working
ps -o pid,lstart,etime,command -p "$(launchctl list | awk '/codesmriti.incremental/ {print $1}')"
```

The real freshness check is the data, not launchd: compare the newest
`run_*.log` in `services/ingestion-worker/logs/` against today's date.

### Logs

- **stdout**: `services/ingestion-worker/logs/launchd.out.log`
- **stderr**: `services/ingestion-worker/logs/launchd.error.log` (incremental)
- **stderr**: `services/ingestion-worker/logs/bdr.error.log` (BDR)

### Manual Trigger

```bash
# Run incremental ingestion now
cd services/ingestion-worker && ./run_incremental.sh

# Regenerate KPI dashboard only
cd services/ingestion-worker && uv run python scripts/generate_kpi.py
```

### Troubleshooting

If KPI page stops updating (or the daily CoS digest stops arriving — same
pipeline, same causes):
1. Check for a **wedged run** first, per "Checking Status" above. A hung
   instance blocks all later runs while still reporting exit 0.
2. Check `launchctl list | grep codesmriti` for non-zero exit codes
3. Check `logs/launchd.out.log` for errors
4. Common issue: subprocess calls must use `sys.executable`, not bare `python`

`run_incremental.sh` bounds every step with a wall-clock watchdog
(`INGEST_TIMEOUT_SECS`, default 10h; also `KPI_TIMEOUT_SECS`,
`DIGEST_TIMEOUT_SECS`), so a wedged run is killed rather than left to suppress
the schedule. On failure or timeout it posts a high-priority alert note to the
CoS inbox using the **`cos` CLI** (`cos doc create --type note --priority high
--tag alert`), which owns the endpoint, schema and its own credentials in
`~/.config/cos/env` — the wrapper hand-rolls no payload and carries no second
copy of the token. Note that `cos` lives in `~/.local/bin`, which is *not* on
the minimal PATH launchd provides, so `run_incremental.sh` adds it explicitly;
drop that and the alert silently no-ops. KPI regeneration and the digest are
both skipped when ingestion fails, so a stale dashboard or a digest of
yesterday's run is never passed off as fresh.

Known precedent: on 2026-07-24 the ingestion Python wedged during interpreter
startup (blocked in `open()` inside `_PyConfig_InitPathConfig`), which cost four
days of runs before it was noticed. The venv and TCC permissions were fine — the
weekly BDR agent kept running normally throughout on the same interpreter.

To reload an agent after editing its plist:
```bash
launchctl unload ~/Library/LaunchAgents/com.codesmriti.incremental.plist
launchctl load ~/Library/LaunchAgents/com.codesmriti.incremental.plist
```
