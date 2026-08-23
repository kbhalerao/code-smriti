# Rolling Ingestion Queue — Design

Status: **all five items shipped 2026-08-23.** Live on com.codesmriti.scan /
.drain / .daily. This document is now a record of why, not a plan.

Replaces the nightly batch (`com.codesmriti.incremental`, 15:05 daily) with a
5-minute tick over a durable per-repo queue, and stops ingestion from starving
interactive callers on the shared Ollama host.

---

## 1. The problem being solved

Three separate failures, one root cause — ingestion is a single unbounded batch
with no work-item state.

**A wedged or killed run suppresses the schedule.** launchd will not start a
second `StartCalendarInterval` instance while one is alive, and reports the last
*completed* exit code, so a hung run looks healthy indefinitely. Partly mitigated
already (`_checkpoint_commit`, SIGTERM → `TerminationRequested`), but the run is
still the unit of recovery.

**Ingestion starves cos.** `cos-web/src/lib/server/flow-select.ts:163` and
`scriven/src/scriven/config.py:30` both target the `structured` role alias — the
same model as `config.py:156`'s chunker. `max_concurrent_files` is 10 against
`OLLAMA_NUM_PARALLEL=4`, so ingestion occupies all four slots *and* keeps six
requests queued inside Ollama. A cos flow-select that should take 0.6s lands
behind up to six ~12s chunker calls.

**A file that fails is a file that vanishes.** `pipeline.py:213` catches the
exception and returns `(None, [], [])` — no `file_index` document at all, while
the commits index advances and declares the repo current. There is also no
per-file wall clock: `llm_timeout_seconds` (900) is *per request*, and a file
makes one request per significant symbol plus chunker passes.

**A chunker failure is indistinguishable from an empty result.**
`llm_chunker._call_llm:411` catches `HTTPStatusError`, catches bare `Exception`,
and returns `"[]"` from both — the identical value it returns when the model
genuinely finds no additional regions. Nothing raises, so
`get_llm_chunks`'s `record_llm_call(success=False)` (`file_processor.py:332`)
never fires either. The only trace is a log line that nothing counts and nothing
carries into the run record.

The blast radius is where it hurts most: the chunker fires on ~65% of files, and
for extensions with no tree-sitter grammar (`.sh`, `.sql`, `.vue`, `.kt`) it is
the *only* source of content. A silent `"[]"` on a shell script puts it in the
corpus with essentially nothing in it, indistinguishable from one the model read
and had no opinion about. **The current failure rate is unknown and unknowable —
there is no counter to read.** Expect the first run after this is fixed to be a
measurement.

---

## 2. Slot discipline

**Ollama has no priority mechanism.** No request priority, no queue reordering,
no preemption; slots are handed out FIFO per model. "Deprioritize ingestion" is
therefore not something the server can be asked for — it can only be implemented
client-side, as *ingestion holds fewer slots*. The pipeline semaphore is the only
place this behaviour can exist.

Cap outstanding chunker calls at **2** (of `OLLAMA_NUM_PARALLEL=4`), leaving two
slots always free for cos and scriven.

Build the gate with its limit read **at acquire time**, not fixed at
construction. Flat-2 and "4 when cos is cold, 2 when it is warm" are then the
same code and the choice is a config value. Default flat.

Accounting note, since it is easy to get backwards: the GPU is saturated at
~118.6 tok/s aggregate, so throughput ingestion gives up is *transferred* to
whoever else is asking, not lost. The only real loss is idle slots while cos is
quiet.

Two distinct wins, worth not conflating:

| ingestion outstanding | worst-case cos wait | chunker throughput |
|---|---|---|
| 10 (today) | ~6 queued × ~12s ≈ 70s | 118.6 tok/s |
| 4 | first slot to free ≈ 12s | unchanged |
| 2 | ~0s | reduced (unmeasured; MoE may fall off worse than linearly) |

10 → 4 is free — it only stops ingestion stacking a queue *inside* Ollama on top
of the slots it already holds. 4 → 2 is the actual reservation.

Measure 2-concurrent on an **idle box** before trusting any throughput figure.

---

## 3. Per-file wall clock

Wrap each file in `asyncio.wait_for(file_timeout_seconds)`.

**On expiry, emit what exists — never drop the file.** Parser symbols keep their
real spans; un-summarised ones fall back to docstring + structure. The degraded
shape already exists (`EnrichmentLevel.BASIC`) and is what `--no-llm` produces
and what `get_llm_chunks` already degrades to on its own errors. A slightly
inaccurate summary is acceptable; an absent file behind a commits index claiming
currency is not.

Mark these `summary_source: "timeout_fallback"`, distinct from ordinary
`"fallback"`. The marker is what makes the failure findable afterwards, and it
is free.

### Prerequisite: failure must be distinguishable from empty

Everything below depends on a failure being *observable*, and in the chunker it
currently is not (§1). `_call_llm` must raise, or return a discriminated result —
`"[]"` cannot continue to mean both "the call failed" and "no additional
regions".

Retry counts differ per layer and are worth not conflating:

| layer | attempts | on final failure |
|---|---|---|
| summarizer — `llm_enricher.generate:268` | **3** (`max_retries=2`), 1s/2s backoff, circuit breaker at 5 consecutive | raises → `generate_symbol_summary` catches → `BASIC` fallback |
| chunker — `llm_chunker._call_llm:411` | **1** | returns `"[]"`, never raises |

The summarizer already retries three times and already degrades gracefully. The
per-file retry below is therefore not "one more attempt" — for the summarizer it
is a fourth, and it is worth making only because the end-of-pass *conditions*
differ, not because the count is higher.

### Retry, in-task and immediate

A file that fails is retried **within the same repo task** — never deferred to a
repair pass or another tick.

Retry at the **end of the repo's file pass**, once the fan-out has drained, not
instantly in place. A file times out because its own batch saturated the slots;
retrying while that batch is still in flight reproduces the condition that
caused it. Retrying the stragglers against free slots usually clears them.

The two failure kinds behave differently and are worth distinguishing:

- **Timeout** — contention, almost certainly transient. The end-of-pass retry
  normally succeeds.
- **Exception** — likely deterministic. The retry reproduces it, and that
  reproduction is the signal.

### Failing loudly without livelocking

A file still failing after its retry is **a bug in the pipeline**. It goes to the
dead-letter queue (§6) for a human to investigate. Nothing retries out of the
DLQ on its own — the point of an entry is that something is wrong that the
pipeline cannot resolve by trying harder.

It must **not** abort the repo task. Aborting leaves the commit un-checkpointed,
so every subsequent tick re-picks the repo and reprocesses it whole — forever.
That is the livelock `_checkpoint_commit` was written to escape, re-entered
through a different door by a single poison file. The repo completes, emits the
degraded document, checkpoints its commit, and the DLQ entry is what gets
attention.

`llm_timeout_seconds` can come **down off 900**. That budget exists only because
requests 5–10 of a batch sat in the server queue (see the note at
`config.py:159`); once outstanding calls never exceed slots there is no server
queue, and a tight per-file budget stops being a source of spurious degradation.

---

## 4. Tick, scan, and processor

Both fire on the same 5-minute tick, as separate processes.

- **Scan** — fetches, diffs against the commits index, populates the queue.
- **Processor** — takes the existing flock, drains whatever is in the queue.

No ordering dependency. If the scan queues an item after the processor has
already looked, it is picked up next tick. Five-minute eventual consistency is
the whole coordination protocol.

Tick interval is deliberately under `OLLAMA_KEEP_ALIVE=30m`, so the ~29GB
chunker weights stay resident between ticks and are never cold-loaded per tick.

### The processor does not fetch

`GitOperations.fetch` (`git_utils.py:113`) is not read-only — it runs
`_ensure_fetch_refspec` (config write) and `git remote set-head origin -a` (ref
write). With scan and processor on the same tick, both fetching the same repo
collide on git's own locks.

So the scan is the **only writer to `.git`**. It records `base_commit` →
`target_commit` on the queue item and the processor works exactly that range.
Three consequences:

- No concurrent `.git` writes.
- Fetching across ~100 repos happens once per tick, not twice.
- The queued file list is **exact**, not advisory — it is what will run, so it is
  also what the dashboard can show.

The processor pins to the **recorded commit hashes**, not "origin's default
branch tip" — it reads `base_commit` and `target_commit` off the queue item and
works that range.

In `incremental` mode this means the worktree is never touched at all.
`get_file_at_commit` (`file_processor.py:97`) is `git show {commit}:{path}`, so
content comes from git objects, and the file list comes from the queue item
rather than a directory walk. Only `full_reingest` and `initial_clone` need a
worktree, because `pipeline.discover_files` walks the filesystem — and a scan's
`git fetch` moves refs, never a worktree, so nothing the scan does can shift the
tree under a running rebuild.

### Locks

Two, both non-blocking, both clean-skip when held:

- the existing ingestion flock — processor,
- a second scan lock — ~100 × fetch is minutes, which does not sit comfortably
  inside a 5-minute tick.

`run_incremental.sh`'s `LOCK_BUSY_RC=2` clean-skip semantics carry over unchanged.

### The watchdog shrinks

`INGEST_TIMEOUT_SECS` is 10h because a killed run used to lose everything. With a
durable queue and per-repo checkpointing a kill costs one item and the next tick
resumes, so the processor can be bounded at ~2h. Wedge detection goes from half a
day to two hours at no cost.

---

## 5. Queue item

One document per repo in `code_kosha`. Authoritative — this is the queue, not a
projection of it.

| field | notes |
|---|---|
| `repo_id` | |
| `mode` | `incremental` \| `full_reingest` \| `initial_clone` |
| `base_commit`, `target_commit` | pinned by the scan; the processor works this range |
| `files` | the **filtered** list from `filter_supported_files`, with per-file state |
| `indexable_changed` | primary count |
| `total_changed` | raw git count, secondary |
| `state` | `queued` \| `leased` \| `done` \| `failed` |
| `attempts`, `last_error` | dead-letters to the existing cos alert path |
| `enqueued_at`, `leased_at` | |

**Order by staleness, oldest successful ingest first.** Today's
`sorted(repos_to_process)` (`updater.py:935`) is alphabetical, so the same repos
run first every night and a watchdog kill always eats the same end of the
alphabet.

**Show `indexable_changed`, not `total_changed`.** The raw git count includes
images, lock files, `.po` catalogues — the numerator that once logged
affiliate-sites as "1064 files changed (765.5%)". Surfacing it as the primary
number re-creates that confusion visually.

**`mode` carries more signal than the count.** A rebuild or a fresh clone has no
meaningful "changed file count" — it is every file — and a `full_reingest` is the
multi-hour item sitting next to two-minute incrementals. It is what explains
queue latency.

---

## 6. Dead-letter queue

**No silent failures.** Anything the pipeline could not complete and could not
resolve by retrying lands in the DLQ, and a human works out why. Nothing drains
it automatically — an entry exists precisely because trying harder is not the
answer.

What lands here:

- a file that failed its in-task retry (§3), timeout or exception,
- a chunker call that failed rather than returned empty (§3 prerequisite),
- a repo-level failure — clone failed, git error, queue item exhausted `attempts`.

One document per `(repo_id, file_path, failure_kind)`. **Key it, don't append** —
a repo that changes daily with one persistently broken file would otherwise
produce an entry a day. Repeats increment `count` and move `last_seen`.

An entry clears when that file next processes successfully. The alert has
already gone out by then, so nothing is lost by clearing, and the DLQ stays a
"what is broken now" view rather than an append-only log that no one reads.
Manual dismissal for the cases that resolve some other way.

### Alerting is aggregated, DLQ entries are not

One cos note per failed file would be a self-inflicted outage. `cos doc create`
embeds every note for vector search — ~1s idle, far worse under load, which is
exactly the 2026-08-21 failure where the alert path blew its own watchdog while
ingestion was still draining off the GPU.

So: DLQ entries go to Couchbase, which is cheap and queryable. **One aggregated
alert per run** — counts by failure kind, the repos involved, and the worst few
files. The dashboard is where the detail is read.

`run_incremental.sh` posts it only when the set of entries **changes**, keyed on a
fingerprint in `logs/.dlq-fingerprint`. A file that is genuinely broken should be
reported once, not every night until someone fixes it; nightly repetition is how
an alert stops being read.

**The DLQ query must run at `REQUEST_PLUS` scan consistency.** N1QL is eventually
consistent by default and the measured lag on this bucket is ~4s, while both
readers run immediately after the write — the runner takes its summary at the end
of the run and the shell wrapper posts its notice seconds later. At default
consistency both reported an empty queue, which would have made the whole
alerting path silent in exactly the way this change exists to prevent.

This matters most on the first run after the chunker fix lands. If chunker calls
have been failing at any rate, that run surfaces all of it at once, and an
un-aggregated alerter would bury the signal in its own volume. Cap DLQ growth
per run the way `ALERT_SPOOL_MAX` caps the spool.

---

## 7. Job publishing and the dashboard

A `/jobs` view on cos.agsci.com showing queued repos with their change counts,
and for the item in flight, its file list.

**Producers publish over HTTP to cos-api; the queue stays authoritative in
`code_kosha`.** Two reasons:

- cos-api already depends on code-smriti (`CODESMRITI_API_URL` in its compose).
  Making the ingestion queue depend on cos would close that into a hard cycle
  around the ingestion loop. Publishing is fire-and-forget and fail-soft — same
  posture as `deliver_cos_alert` — so a dashboard that cannot be reached never
  stalls the queue.
- Scriven is Postgres (`scriven/src/scriven/config.py:12`) with no Couchbase
  client, so HTTP is its only option. One path for both is what makes this
  reusable rather than two integrations that happen to share a table.

**Do not publish per-file transitions.** A few hundred POSTs per repo over a
channel meant to be ignorable. File state lives in the queue item, which the
processor already has a Couchbase handle for; cos receives a throttled snapshot —
`{mode, done, total, in_flight: [...], degraded: [...]}` every few seconds or on
repo transition. That shape also fits scriven's "processing 40 messages", so the
generic contract survives.

`degraded` is where §3's timeout fallbacks surface. Without it they are only
findable by querying `summary_source`.

The page also carries the **DLQ as a "needs attention" view** — that is where §6
is read, and the only place the aggregated alert points to.

---

## 8. Build order

All five shipped. Items 3-5 in `b7b3072b`, `324148a7`, `cb06cb58`.

1. ~~**Gate**~~ — **built**. `llm_gate.py`, applied to the chunker
   (`structured`) and the enricher (`general`); cos-web's chat shares the latter,
   so gating only the chunker would have left half the contention in place.
2. ~~**Make file-level failure visible.**~~ — **built**. `_call_llm` and
   `_parse_llm_response` now raise `ChunkerCallFailed` instead of returning `[]`;
   `ProcessedFile`/`FileFailure` carry failures out of processing;
   `pipeline.process_file_bounded` bounds each file and degrades to an LLM-free
   reprocess marked `timeout_fallback`; end-of-pass retry on both the full and
   incremental paths; survivors land in the DLQ (`v4/dlq.py`, `--dlq`).
   `llm_timeout_seconds` is still 900 — see Outstanding below.
3. ~~**Queue + DLQ**~~ — **built**. — item schema, scan/drain split, 5-minute tick, staleness
   ordering, attempts exhausted → DLQ, aggregated per-run alert, watchdog to ~2h.
4. ~~**Job publishing + cos-web `/jobs`**~~ — **built**, ahead of 3 by request. including the DLQ view, generic enough
   for scriven.
5. ~~**`Nice`** in the plist~~ — **built**, folded into the tick plists. for the CPU-bound embedding phase — the launchd path
   disables MPS, so embedding is CPU torch plus 4 tree-sitter threads and does
   compete with Couchbase and cos-web. Use the plist `Nice` key, **not**
   `ProcessType: Background`, which also throttles disk I/O hard enough to
   matter. This is a real but narrow win: it does nothing for the chunker phase,
   which is out-of-process and dominates wall clock.

1 and 2 are independent of the queue and fix a corpus correctness bug rather than
scheduling. They went first.

### What the build changed about this design

Three things here were decided on reasoning and corrected by measurement. They
are left in place above, with the corrections beside them, because the reasoning
was not silly — it was just wrong, and the shape of the error is the useful part.

- **"Both halves on the same 5-minute tick."** The scan takes 5m17s serial for
  288 repos, so that would have been ~3,400 git fetches an hour, back to back,
  forever. Parallelising it (8 threads, I/O-bound) brought it to ~46s, which
  removed the *duration* objection but not the *volume* one. Scan runs at 15
  minutes, drain at 5.
- **"A drain tick is cheap when the queue is empty."** It was not: building the
  runner loads the embedding model, so a no-op tick cost 30+ seconds. It now asks
  the queue first with a bare Couchbase client — 2.3s.
- **The queue needed an index, and then a predicate.** `counts()` scanned the
  whole 180K-document corpus at 25s until it gained `state IS NOT MISSING`, because
  N1QL will not use an index whose leading key has no predicate. Both indexes now
  live in `scripts/ensure_query_indexes.py`, whose docstring already carried that
  exact lesson from a previous encounter with it.

And one consequence the design did not anticipate at all: **the daily digest read
"the most recent ingestion_run doc"**, which is the same sentence as "what
happened today" only while a day holds one run. Arming the tick without changing
it would have silently reduced the digest to a report on the last five minutes.
It reads a 24-hour window now.

### Outstanding from items 1-2

- **`llm_timeout_seconds` is still 900.** It can come down now that outstanding
  requests never exceed slots (§2), but the right value should be measured off a
  real run under the new gate rather than guessed.
- **`file_timeout_seconds` (300) is a first estimate.** Roughly 25 symbol
  summaries at ~12s. The first run under it will say whether that is generous or
  tight; a wrong value shows up as `timeout` entries in the DLQ.
- **Throughput under the 2-slot gate is unmeasured.** `structured` is MoE and may
  fall off worse than linearly below 4 concurrent. Benchmark on an idle box.

---

## Deliberately not built

- **A deferred repair pass.** Retry happens in-task (§3) or not at all. A file
  that survives its retry produces an alert, and a degraded document that is
  corrected the next time its repo changes.
- **Adaptive slot sizing.** Mechanism is in place (limit read at acquire); only
  the value is fixed.
- **Closing the rebuild availability hole.** `ingest_repository` still deletes a
  repo's documents before writing new ones, so a `full_reingest` leaves the repo
  absent from the corpus for hours. Raising `DEFAULT_REINGEST_THRESHOLD` to 0.5
  reduced how often that window is entered; closing it means writing the new
  generation and swapping. Still open, unchanged by this design.
