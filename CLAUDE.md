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

The model is **`Qwen/Qwen3-Embedding-0.6B`**, stored **Matryoshka-truncated to 768
dimensions**, loaded in-process via sentence-transformers on both sides. It
replaced `nomic-embed-text-v1.5` on 2026-08-19 on measured retrieval: over 5,000
documents and 300 natural-language queries, nomic scored MRR 0.752 / recall@1
0.667 against 0.886 / 0.817 — a 7.7-sigma difference. Larger Qwen variants add
nothing measurable (4B 0.885, 8B 0.888, under one sigma), so the smallest is used.

**Do not hand-write prefixes.** Model, prefixing, dimensionality and batching all
live in one module, and every producer must go through it:

- `services/ingestion-worker/embeddings/convention.py` — the definition
- `services/api-server/app/rag/embedding.py` — the query-side mirror

They are separate files because the services cannot share code. The agreement
between them is enforced at runtime by `assert_corpus_matches()`, which reads an
`embedding_manifest` document the re-embed writes into the corpus and logs loudly
on mismatch.

That check exists because **the obvious guard does not work**: a dimension check
passes while the space changes underneath it, since 768 truncated from Qwen is
the same shape nomic produced and a completely different space. Vectors from two
models still yield a dot product — a number, not a measurement — and nothing
raises. Prefixing is asymmetric and model-specific: nomic took literal
`search_query:` / `search_document:` on both sides; Qwen3-Embedding takes an
instruction on the query side only. Getting it wrong cost 0.11 AUC when measured.

All embeddings are **normalized to unit length** (L2 norm = 1.0) for the FTS
vector index, which uses `dot_product` — that is the cosine only for unit
vectors, and Matryoshka truncation denormalises, so truncation always
renormalises.

Symbol embeddings carry the summary plus up to `CODE_CHARS_FOR_EMBEDDING` (8,000)
characters of source. Batching is bounded by both a token budget and an
**attention** budget (`B x L^2`), because attention is quadratic and a single
token budget once asked for a 182 GiB buffer. Budgets are tunable via
`EMBED_MAX_TOKENS_PER_BATCH`, `EMBED_MAX_ATTENTION_PER_BATCH`,
`EMBED_MAX_ITEMS_PER_BATCH`.

Changing any of this means re-embedding the whole corpus
(`scripts/reembed_corpus.py`, ~3.5h for 180K documents) **and** CoS, whose
`cos_vector_index` runs on the same model. Park the incremental LaunchAgent while
that runs or it writes vectors in the new space into a corpus still in the old one.

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

## Local LLM Setup (Ollama)

Ollama provides local LLM inference on port **11434**, proxied via nginx at
`/llm/*` (`upstream llm { server host.docker.internal:11434; }` in
`services/api-gateway/nginx.conf`).

**LM Studio is no longer used.** It previously served this role on port 1234, and
the leftovers are misleading: the `lms` CLI is still on PATH and
`~/.lmstudio/startup.sh` still exists, but `com.lmstudio.server.plist` is gone and
nothing listens on 1234. If a doc or script tells you to run `lms server start`,
it is stale.

### Auto-start Configuration

| LaunchAgent | Purpose |
|---|---|
| `com.ollama.server` | The server itself. All operative config is in its `EnvironmentVariables`. |
| `com.ollama.warmup` | Runs `~/.ollama/warmup.sh` after boot: waits for `/api/version`, rotates logs, and asserts exactly one `ollama serve` is running. |

That split-server assertion matters. Two `ollama serve` processes will both answer,
each with its own runners and its own copy of the weights, and the only symptom is
memory pressure plus inconsistent behaviour depending on which one a caller hit.

Current server environment:

```
OLLAMA_HOST=0.0.0.0:11434     OLLAMA_NUM_PARALLEL=4
OLLAMA_CONTEXT_LENGTH=16384   OLLAMA_MAX_LOADED_MODELS=6
OLLAMA_FLASH_ATTENTION=1      OLLAMA_KV_CACHE_TYPE=q8_0
OLLAMA_KEEP_ALIVE=30m
```

`OLLAMA_CONTEXT_LENGTH` and `OLLAMA_NUM_PARALLEL` are load-bearing for ingestion
throughput and are explained in **Ingestion LLM Serving** below. Do not change
either without reading it.

**Editing this plist:** `plutil -lint` is not a sufficient check. It accepts `--`
inside an XML comment, which is illegal XML and is rejected by stricter parsers.
Validate with both:

```bash
plutil -lint ~/Library/LaunchAgents/com.ollama.server.plist
python3 -c "import plistlib;plistlib.load(open('$HOME/Library/LaunchAgents/com.ollama.server.plist','rb'))"
```

### Models

Models are addressed by **role alias**, never by raw tag — `general`, `structured`,
`class-30b`, `class-80b`, `code`, `embed`, `ocr`, `translate`. Aliases are defined
in `~/code/ollama/aliases/*.Modelfile` and swapped with
`make repoint NAME=<role> MODEL=<tag>`, so the git diff records the change.

Two roles matter to this project:

| Role | Backing model | Engine | Used by |
|---|---|---|---|
| `general` | gemma4:26b-nvfp4 | MLX (safetensors) | enrichment/summaries — **cannot batch** |
| `structured` | gemma4:26b-a4b-it-q8_0 | GGUF | LLM chunker; also Listings AI extraction |

The engine is not cosmetic: constrained decoding (`json_schema`) is enforced only
on GGUF. The MLX runner accepts a schema and silently ignores it. Check before
pointing anything schema-dependent at a role — note that `ollama show` does **not**
report the format, so grepping its output returns nothing whether or not the model
is GGUF. Use the API, which is what `llm_chunker.py` itself checks:

```bash
curl -s localhost:11434/api/show -d '{"model":"structured"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['details']['format'])"
```

`/opt/homebrew/bin/ollama` is a symlink to
`/Applications/Ollama.app/Contents/Resources/ollama`. That is deliberate — a real
brew-installed ollama binary ships without `llama-server` and returns 500 on GGUF
and embedding calls. Keep the symlink; do not `brew install ollama` over it.

### The reranker on 11435

Port **11435** is a separate homebrew `llama-server` serving the cross-encoder,
under `~/Library/LaunchAgents/com.smriti.reranker.plist`. It is not ollama and
does not appear in `ollama ps` or `ollama list`:

```
/opt/homebrew/bin/llama-server \
  -m ~/.local/share/llm-infra/models/qwen3-reranker-0.6b-q8_0.gguf \
  --alias qwen3-reranker-0.6b --reranking --host 0.0.0.0 --port 11435
```

It served `bge-reranker-v2-m3` from an ollama *blob* until 2026-08-21, which made
`ollama rm` on the tag owning that blob able to break it at next restart. The
model is a standalone file now and that hazard is retired.

`46669075` moved the api-server onto this service (from an in-process
`ms-marco-MiniLM-L-6-v2`) and wired it into **both** call sites — `search_code`
and the RAG agent — gated by `RAG_RERANK_ENABLED`. Reranking now decides the
final order of `search_codebase` results, over `content` at
`max_candidates=20`. The gateway also exposes it at `location = /llm/rerank`
for off-box callers; being an exact-match sibling of `/llm/` it inherits none of
that block's guards and carries its own `$llm_pass`.

Two properties of the current model that a caller has to respect:

- Scores arrive **already normalised to 0-1** (llama.cpp softmax over the 2-way
  classifier), so do not sigmoid them. `app/rag/reranker.py` carries the
  calibration note: the distribution is bimodal, confident judgements pin to the
  rails, and a cutoff belongs on held-out queries rather than a round number.
- No `-b`/`-ub` is passed, so the physical batch is the **512-token default**,
  and llama-server scores each (query, document) pair as one input. A single pair
  over 512 tokens fails the *whole request* with `input (N tokens) is too large
  to process`. Restart with `-ub 2048 -b 2048` to lift it. Slots and context are
  already adequate (4 slots, 40960 total).

**That 512-token ceiling is currently biting production.** Measured 2026-08-21
against the live corpus, in exactly the shape `search_code` sends (20 documents,
`content_max_chars=1500`):

| level | result |
|---|---|
| `symbol_index`, `file_index` | 200 OK — summaries are ~250-550 chars |
| `document`, `spec` | **500** — chunks are 1,867-3,837 chars; even truncated to 1,500 they reach 531-1,164 tokens |

`CrossEncoderReranker.rerank` catches and returns bi-encoder order on any error,
so `doc` and `spec` searches silently receive **no reranking at all** while
`RAG_RERANK_ENABLED` reports otherwise. Graceful degradation is the right
behaviour; not knowing it is degraded is the problem. Either raise `-ub` or drop
`content_max_chars` below ~1,200.

**And where it does work, it measurably hurts.** `scripts/benchmark_retrieval.py
rerank` (300 queries, 5,000-document haystack, the harness that decided the
embedding migration) against the shipped configuration — K=20, scoring
`content`, head reordered and tail appended:

| config | MRR | R@1 |
|---|---|---|
| Qwen3-Embedding-0.6B alone | 0.967 | 0.950 |
| + rerank K=20 | 0.950 | **0.917** |
| + rerank K=20, query instruction | 0.947 | 0.910 |
| + rerank K=50 | 0.953 | 0.920 |
| + rerank K=20, keyword-style queries | 0.941 | 0.907 |

Perfect reordering of the same pool would score 1.000, so the headroom was there
and the reranker spent it. This is **not noise, but only the paired test shows
that**: unpaired, 0.950 vs 0.917 on n=300 gives p ~ 0.10, because 273 of 300
queries never move and inflate the variance. Paired — McNemar broke 14 rank-1
hits and rescued 4, p = 0.031; Wilcoxon on reciprocal rank p = 0.036; bootstrap
95% CI on the R@1 delta [-0.060, -0.007], excluding zero.

The likely cause is that the bi-encoder baseline is already at R@1 0.950 and the
reranker reads `content`, which holds the **summary** — the same text the vector
was built from, since the pipeline embeds summary+code but persists only the
summary. It is stronger scoring of identical evidence, with far more ways to
break a correct top hit than to fix a wrong one. Reranking source code — the obvious objection, since
the bi-encoder never sees it at query time — was tested once `-ub` was raised
and is **worse, not better**: 0.863 R@1 on summary+code and 0.833 on code alone,
against 0.917 for summaries. The damage scales with how much code the
cross-encoder reads, so no variant of this remains worth trying.

Re-measure before trusting any timing here: the numbers above were taken while
ingestion was running, so quality figures are sound (contention does not move
MRR) but the 708-1798 ms/query is not.

### Troubleshooting

If the LLM proxy returns 502:

1. Check the server is up: `curl -s localhost:11434/api/version`
2. Check exactly one is running: `pgrep -fl "ollama serve"` — more than one is a bug
3. Reload if needed:
   `launchctl unload ~/Library/LaunchAgents/com.ollama.server.plist && launchctl load ~/Library/LaunchAgents/com.ollama.server.plist`
4. If Colima's `host.docker.internal` is stale, restart Colima: `colima stop && colima start`

A reload drops every in-flight request. Check for a live ingestion first
(`cd services/ingestion-worker && ./.venv/bin/python incremental_v4.py --status`);
a reload mid-run kills its LLM calls.

Killing `ollama serve` can leave its model runners orphaned on `ppid 1`, still
holding their weights. After any restart:

```bash
ps -ax -o pid,ppid,rss,command | grep -E "[o]llama runner|[l]lama-server"
# anything with ppid 1 that is not the :11435 reranker is an orphan — kill it
```

### Manual Commands

```bash
# Server status and what is resident (format, context, VRAM)
curl -s localhost:11434/api/version
curl -s localhost:11434/api/ps | python3 -m json.tool

# What actually loaded — slot count is decided at load time, not by the env var
pgrep -fl llama-server        # look for the real -c and -np

# Models
ollama list
ollama show structured --modelfile

# Test from host
curl -s localhost:11434/v1/models

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

## Landing Page and the KPI Dashboard

`landing/` is served unauthenticated at `location /` on the smriti.agsci.com
vhost, which is public.

`index.html` is the technical brief. It was scrubbed for public release
(`d294bb19`) and its only external references are to this project's own public
repository. Keep it that way.

`kpi.html` is generated into the same directory by `scripts/generate_kpi.py` and
was never part of that review. It tabulates repository names against commit
counts — client identities and their relative activity. As of 2026-08-19 it is
restricted to the LAN by an exact-match location in the gateway:

```nginx
location = /kpi.html {
    if ($lan_only = 0) { return 404; }
    root /usr/share/nginx/html;
}
```

Two things about that guard:

- It uses `$lan_only`, **not** `$llm_allowed`. The latter includes
  `allowlist.conf`, which names third-party production infrastructure, and pairs
  with a shared secret. Both are correct for inference endpoints and wrong for a
  page listing client repositories.
- It returns 404 rather than 403, so an off-LAN caller does not learn the page
  exists.

The dashboard regenerates on every incremental run, so deleting the file does not
keep it deleted — the gateway rule is what holds. If a genuinely public metrics
page is ever wanted, `generate_kpi.py` needs to emit aliases or hashes instead of
repository names; the restriction is a containment, not a fix.

Testing it needs a LAN address, because requests from the host arrive NAT'd
through the Docker bridge as 172.28.0.1 and are correctly refused:

```bash
curl -o /dev/null -w '%{http_code}\n' -H 'Host: smriti.agsci.com' http://localhost/kpi.html          # 404
curl -o /dev/null -w '%{http_code}\n' -H 'Host: smriti.agsci.com' \
     -H 'X-Forwarded-For: 192.168.11.29' http://localhost/kpi.html                                    # 200
```

Editing `nginx.conf` requires `docker-compose restart api-gateway`, never just a
reload — see the single-file bind mount note above.

## Scheduled Ingestion (LaunchAgents)

Code ingestion runs automatically via macOS LaunchAgents. Both must remain loaded for the system to stay current.

### LaunchAgents

| Agent | Schedule | Purpose |
|-------|----------|---------|
| `com.codesmriti.scan` | Every 15 min | Fetch, diff, and queue what needs ingesting. The only half that talks to git. |
| `com.codesmriti.drain` | Every 5 min | Work the durable queue under the ingestion lock. Never fetches. |
| `com.codesmriti.daily` | Daily 15:05 | KPI dashboard, digest, dead-letter notice. **No ingestion.** |
| `com.codesmriti.bdr` | Weekly Sun 16:00 | BDR (Business Development Records) enrichment |

`com.codesmriti.incremental` — the old single nightly job — is **booted out and
`launchctl disable`d**, not merely unloaded, so it does not return at login. If it
is ever wanted back it needs `launchctl enable` first; a plain `load` will look
like it worked and silently do nothing.

The split is the point. Ingestion is a durable queue worked continuously
(`docs/INGESTION_QUEUE_DESIGN.md`), so push-to-indexed is bounded at ~15 minutes
rather than 24 hours, and a watchdog kill costs the one repo in flight instead of
the whole backlog. What is left that is genuinely *daily* — the KPI page, the
digest, the DLQ notice — is `run_incremental.sh` with `RUN_STAGE=report`.

Both cadences matter for different reasons. The drain is frequent because it is
nearly free when idle: it asks the queue with a bare Couchbase client and exits in
~2s, rather than building V4Pipeline and loading the embedding model to discover
there is nothing to do. The scan is *less* frequent because it is 288 `git fetch`es
however fast it runs — parallelised it takes ~46s (5m17s serial, `SCAN_CONCURRENCY`
defaults to 8), but at five-minute intervals that would be ~3,400 fetches an hour
aimed at GitHub.

The digest reads a **window**, not the last run. Under a tick the newest
`ingestion_run` doc is the last drain — often one repo, sometimes none — so
`generate_daily_digest.py --since-hours 24` merges every run in the window. A repo
takes its status from the most significant run it appeared in: updated at 09:00
and skipped at 14:55 is still "updated today".

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

**The alert is retried and spooled, because one attempt is not enough.** `cos
doc create` embeds the note for vector search, and the alert fires immediately
after the ingestion tree is SIGTERMed, while its abandoned chunker requests are
still draining off the GPU. On 2026-08-21 that cost a 10h ingestion timeout its
notification entirely: the alert blew its own 120s watchdog (exit 124) and the
outage existed only in `launchd.out.log`. The identical call takes ~1s on an idle
box, so the failure mode is contention at exactly the moment the alert path is
used. `deliver_cos_alert` now makes `ALERT_ATTEMPTS` tries (default 3) spaced by
`ALERT_BACKOFF_SECS` (default `"60 300"`, space-separated in `.env`), each still
bounded by `ALERT_TIMEOUT_SECS`; if all fail it writes the note to
`logs/alert-spool/`. Every run calls `drain_alert_spool` **before** ingestion
starts — the quietest the box gets — so an alert that could not be sent is sent
late rather than lost. Draining stops at the first failure instead of burning a
watchdog budget per file, and the spool is capped at `ALERT_SPOOL_MAX` (20,
oldest dropped). If an alert seems to be missing, look in `logs/alert-spool/`.

**A killed run banks the repos it finished.** It did not used to. The commits
index (`repo_commits_index`) is what decides whether a repo has changed, and
`IngestionRunner._save_run_record` writes it from a results list that only exists
once the whole repo loop returns — so a watchdog kill discarded every completed
repo, and the watchdog is how a wedged run is *supposed* to end. Compounding it,
Python's default SIGTERM disposition terminates without running `finally`, so the
run record was not written either. On 2026-08-21 that cost six fully ingested
repos (their documents were in the corpus, the index still pointed at the previous
day); Aug 20 alone lost three more runs the same way, re-ingesting `chief-of-staff`
four times in two days. Left alone it is a livelock: once the backlog exceeds
`INGEST_TIMEOUT_SECS` the run restarts from zero every night and never drains.

Two changes: `updater._checkpoint_commit` writes each repo's commit to the index
as that repo completes (best-effort — a failure there costs a re-ingest, not the
loop), and `runner` converts SIGTERM into `TerminationRequested`, a **BaseException**
so the pipeline's broad `except Exception` handlers cannot swallow a watchdog kill
and mistake it for a per-file error.

`scripts/reconcile_commits_index.py` repairs an index that has already drifted,
moving entries forward from each repo's `repo_summary`. Note the two records read
different refs — the index stores `origin/HEAD`, `repo_summary.commit_hash` stores
the worktree's `git rev-parse HEAD` — so they diverge whenever a clone is not
anchored to origin/HEAD. The script only moves an entry forward, only on proof
(ancestry, or the corpus commit being current `origin/HEAD`), and only for repos
that actually have `file_index` documents; use `--only` rather than a blanket run.

Known precedent: on 2026-07-24 the ingestion Python wedged during interpreter
startup (blocked in `open()` inside `_PyConfig_InitPathConfig`), which cost four
days of runs before it was noticed. The venv and TCC permissions were fine — the
weekly BDR agent kept running normally throughout on the same interpreter.

To reload an agent after editing its plist:
```bash
launchctl bootout gui/$(id -u)/com.codesmriti.drain
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.codesmriti.drain.plist
```

## Incremental vs Full Re-ingestion

The goal is narrow: the corpus should match the repo at `origin/HEAD`, with no
work beyond what that requires. `process_repo` step 7 decides, and until
2026-08-22 it decided badly in three separate ways.

**The change ratio compared two different populations.** The numerator,
`changes.total_changed`, counts every path `git diff --name-status` reports —
images, lock files, `.po` catalogues, CSV fixtures. The denominator counts
`file_index` documents, which exist only for files the pipeline ingests.
affiliate-sites logged `1064 files changed (765.5%)` against a corpus of 488.
Both sides are now filtered through `filter_supported_files`, which step 8
needed anyway and used to recompute — so the fix costs nothing.

**A repo where nothing indexable changed is now a skip.** It advances the stored
commit and does no work. Previously 40 changed PNGs against a 300-file corpus
read as 13.3% and rebuilt the entire repo.

**An absent corpus is an explicit branch**, not a `or 1` divide-by-zero guard.
That guard reached the right answer by accident and logged nonsense doing it
(`3 files changed (150.0%)`).

`DEFAULT_REINGEST_THRESHOLD` is **0.5**, raised from 0.05. The threshold is not a
cost trade-off and treating it as one is what kept it low: a full re-ingest
processes every file in the repo, an incremental only the changed ones, and both
regenerate module and repo summaries afterwards — so full is a strict superset of
incremental's work below 100%. There is no crossover to tune toward, only the
point where a bulk delete-and-rebuild earns its cost in drift cleared. At 5% that
point was set absurdly early: 41% of change-bearing repos were rebuilt whole, 58%
of those for a change under 20%. Of 308 historical rebuilds, 207 (67%) become
incrementals at 0.5, before the numerator fix moves more.

**A rebuild is also an availability hole, which is the part that is easy to
miss.** `ingest_repository` deletes the repo's documents before it writes new
ones, so for the hours it runs the repo is not in the corpus at all —
farmworthdb spent ~3 hours rebuilding 622 files to absorb 39 changed ones, and
was absent from search throughout. A run killed inside that window loses the repo
outright, which is exactly what happened to wanderers-return on 2026-08-21.
Raising the threshold reduces how often that window is entered; it does not close
it. Closing it means writing the new generation and swapping, and is still open.

Drift is what full re-ingestion legitimately clears, and it accumulates with the
*number* of incremental updates, not the size of any one of them — so a churn
ratio is the wrong axis for it. There is no periodic rebuild yet;
`scripts/reconcile_symbol_documents.py` and `newest_per_key` in
`_regenerate_summaries` are what currently hold the line.

### Shell scripts, and why a new extension needs a backfill

`.sh`, `.bash` and `.zsh` became indexable on 2026-08-22. Adding an extension
takes **two** edits, and the second is easy to miss:

- `supported_code_extensions` in `config.py` — gates discovery in
  `pipeline.discover_files` and the incremental filter in
  `updater.filter_supported_files`.
- the map in `CodeParser.detect_language` — without it `detect_language` returns
  `None`, `parse_file` bails at `if not language: return []` before it reads the
  file, and `metadata.language` is never set.

There is no tree-sitter grammar for shell, so these files yield no parser symbols
and reach the corpus through the LLM chunker. That is not a special case: `.sql`,
`.vue` and `.kt` are already listed with no grammar behind them. Adding
`tree-sitter-bash` would buy real function extraction and is the only reason to
take a new dependency here.

**The extension change on its own reaches almost nothing.** Incremental ingestion
is change-driven, so a shell script is indexed only if someone happens to edit it;
every other one stays invisible. This is the same trap the class-extraction parser
fix hit — correct for weeks while the corpus knew nothing about it. Raising
`DEFAULT_REINGEST_THRESHOLD` to 0.5 the same day made it worse, because accidental
full re-ingests were the only thing that would have swept them in.

`scripts/backfill_shell_files.py` closes it: 393 files across 95 repos at the time
of writing. It processes only the missing files, through the same
`file_processor.process` the incremental path uses, and deliberately does **not**
move the commits index — the repo is still indexed at the same commit, it just
gains files it should have had. It takes the ingestion lock while applying, since
ingestion is GPU-bound at ~94% and the 15:05 LaunchAgent would otherwise start
midway; `run_incremental.sh` treats a held lock as a clean skip. `--dry-run` is
the default and needs neither the lock nor the embedding model.

Scale of what was missing: `soilaction307/soilaction-platform` is a deployment
repo whose substance is 43 shell scripts, and its corpus held **9** documents.

## Ingestion LLM Serving

Ingestion's wall clock is dominated by the **LLM chunker**, not the enricher.
Profiled 2026-08-20: the "last resort" chunker fired on **65% of files** at ~1.8
passes each, which is essentially the whole per-file cost.

The chunker model is therefore the highest-leverage knob in the pipeline, and it
is set in code — `llm_chunker_model` in `services/ingestion-worker/config.py`,
default `structured`. That file carries the full decision record and the seeded
yield harness; read it before changing the model. The short version:

| chunker | arch | slots | aggregate | same 67 files |
|---|---|---|---|---|
| `class-30b` | qwen35 dense 27.3B | 1 | 20.8 tok/s at any concurrency | 55m17s, 199 chunks |
| `structured` | gemma4 26b-a4b **MoE** | 4 | 118.6 tok/s at 4 concurrent | **11m00s**, 185 chunks |

5.03x wall clock for ~7% fewer chunks (~16% on the seeded harness). A dense model
cannot batch its way out of this — it is pinned near 20.8 tok/s regardless of how
many files the pipeline sends.

Two facts about the serving layer that are **not** in this repo and will be lost
in a rebuild:

- `OLLAMA_CONTEXT_LENGTH=16384` in `~/Library/LaunchAgents/com.ollama.server.plist`.
  ollama sizes KV as slots x context and decides the slot count **at model load
  time against free memory**, silently, so a large context can cost the `-np 4`
  grant. `class-30b` was stuck at `-np 1` this way for entire runs. Sizing floor:
  `MAX_OUTPUT_TOKENS` is 8000 and prompts reach ~4,038 tokens, so 8192 would
  overflow — and ollama passes `--context-shift`, which drops the *head* of the
  context (instructions and JSON schema) without erroring.
- Model roles are **aliases** in `~/code/ollama/aliases/`, swapped with
  `make repoint NAME=... MODEL=...` so the git diff documents the change. Address
  the alias (`structured`), never the raw tag — calling both loads the weights
  twice.

Never trust the env var for slot count. Check what actually loaded:

```bash
pgrep -fl llama-server        # look for the real -c and -np on the loaded blob
curl -s localhost:11434/api/ps | python3 -m json.tool   # format must be gguf
```

The chunker requires a **GGUF** target: constrained decoding (`json_schema`) is
enforced only by ollama's GGUF engine, and the MLX/safetensors runner accepts the
schema and silently ignores it. `llm_chunker.py::_warn_if_schema_unsupported`
guards this at the first call.

`structured` is shared with Listings AI, which is experimental and lower priority
than ingestion — it queues. A dedicated runner is not available by tagging: two
tags on one blob cannot coexist (ollama evicts one), and a second `ollama serve`
instance costs a full ~28.9GB weight copy for queue isolation with no throughput
gain, since one GPU means instances split bandwidth.

Benchmark **only on an idle box**. A probe run during ingestion showed what looked
like a 2.5x win from a config change; the idle A/B showed zero difference. It was
measuring the queue.
