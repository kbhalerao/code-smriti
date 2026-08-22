"""
Configuration for Ingestion Worker
"""

import os
from pydantic_settings import BaseSettings


class WorkerConfig(BaseSettings):
    """Worker configuration loaded from environment"""

    # Couchbase Configuration
    couchbase_host: str = os.getenv("COUCHBASE_HOST", "localhost")
    couchbase_port: int = int(os.getenv("COUCHBASE_PORT", "8091"))
    couchbase_username: str = os.getenv("COUCHBASE_USERNAME", "Administrator")
    couchbase_password: str = os.getenv("COUCHBASE_PASSWORD", "")
    couchbase_bucket: str = os.getenv("COUCHBASE_BUCKET", "code_memory")

    # Repository Storage Path
    repos_path: str = os.getenv("REPOS_PATH", os.path.expanduser("~/codesmriti-repos"))

    # Embedding Configuration
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "local")  # "local" or "ollama"
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    # Model is cached locally after first download
    embedding_dimensions: int = 768

    # LLM model for all summary generation (module/repo summaries, BDR, enrichment).
    # `general` is an ollama ALIAS, currently resolving to gemma4:26b-nvfp4 — check
    # with `ollama show general`, don't trust this comment. It is pinned/resident so
    # ingestion and RAG share one loaded model: no reload stalls, no cross-model GPU
    # contention. Override per deployment via LLM_MODEL.
    llm_model: str = os.getenv("LLM_MODEL", "general")

    # LLM serving endpoint. Provider-agnostic: any OpenAI-compatible server
    # exposing /v1/responses (ollama, LM Studio, vLLM, ...). Configure via env.
    llm_base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:11434").rstrip("/")
    llm_provider: str = os.getenv("LLM_PROVIDER", "ollama")  # informational label
    llm_reasoning_effort: str = os.getenv("LLM_REASONING_EFFORT", "none")

    # Model for the LLM chunker specifically. Two hard requirements, both measured:
    #
    # 1. It must be a **GGUF** build. Constrained decoding (`json_schema` response
    #    format) is implemented only by ollama's GGUF engine; the MLX/safetensors
    #    runner accepts the schema and silently ignores it, returning fenced prose
    #    that then fails to parse. `ollama show` does NOT report the format, so
    #    grepping it returns nothing either way; ask the API, as
    #    _warn_if_schema_unsupported does:
    #      curl -s localhost:11434/api/show -d '{"model":"structured"}' \\
    #        | python3 -c "import sys,json;print(json.load(sys.stdin)['details']['format'])"
    # 2. It must not be a low-precision quant. Q4_0 costs ~18% of extractions
    #    against the same weights at higher precision — a bigger loss than the
    #    schema itself imposes (~15%).
    #
    # Measured on 20 files x 3 passes, chunks kept (0 malformed unless noted):
    #   gemma4 Q4_0 GGUF + schema        99
    #   gemma4 Q4_0 GGUF, no schema     117
    #   gemma4 Q8_0 GGUF + schema       130   <- this default
    #   gemma4 nvfp4 MLX, schema ignored 142 (136 clean, 1 parse failure)
    #
    # Q8_0 recovers the quantization loss while keeping enforcement: within ~4% of
    # the unconstrained MLX yield, with zero parse failures and zero malformed
    # chunks. Everything else in the pipeline stays on the faster MLX `general`.
    #
    # Addressed by ROLE ALIAS, not raw tag. Aliases live in ~/code/ollama/aliases/
    # (swap targets with `make repoint NAME=... MODEL=...`). Using the alias also
    # avoids ollama's duplicate-weight trap: runners are keyed by tag, so an alias
    # and its underlying tag load the same weights twice if both are called.
    #
    # Moved BACK to `structured` on 2026-08-20, reversing the 2026-08-18 switch to
    # `class-30b`. This is a deliberate speed-for-yield trade; read both halves
    # before changing it again.
    #
    # a) Why it was on `class-30b`. That model (qwen3.8:27b-q8_0, dense ~27B
    #    active) beat `structured` (gemma4 26B-A4B, ~4B active) on yield across
    #    all three seeds of the 20x3 harness — scripts/eval_chunker_models.py:
    #
    #      seed  structured  class-30b  delta
    #        0      153         164      +11
    #        1       98         122      +24
    #        2       92         112      +20
    #      total    343         398      +55  (+16.0%)
    #
    #    Per-file 31 better / 14 equal / 15 worse over 60 files — broad, not a few
    #    spikes. That result still stands and is the cost of this default.
    #
    # b) Why it moved back anyway. The chunker is the single largest cost in a
    #    run, and `class-30b` was far slower than the note above assumed. Profiled
    #    2026-08-20 on run_20260820_083610: the "last resort" chunker fired on
    #    **65% of files** at ~1.8 passes each (55 of 84 files, 99 invocations),
    #    which is essentially the entire 31-49 s/file wall clock. Idle benchmarks,
    #    identical prompts, aggregate tokens/sec:
    #
    #      class-30b  dense, 1 slot   20.7 / 20.8 / 20.8  at 1 / 2 / 4 concurrent
    #      structured MoE,   4 slots  61.9 / 90.2 / 118.6 at 1 / 2 / 4 concurrent
    #
    #    A dense model cannot batch its way out: `class-30b` is pinned near
    #    20.8 tok/s at ANY concurrency, so the pipeline's concurrent files just
    #    queue. Confirmed end-to-end on identical work (PeoplesCompany/aca-portal,
    #    same 67 files, back to back):
    #
    #      class-30b   55m17s   49.5 s/file   199 chunks   398 semantic units
    #      structured  11m00s    9.9 s/file   185 chunks   370 semantic units
    #
    #    5.03x wall clock for ~7% fewer chunks on that repo (~16% on the seeded
    #    harness). Accepted because a full labcore re-ingest is ~1,710 files: the
    #    difference is a ~5h run versus a ~23h one, and a run that does not finish
    #    inside its window is worth far less than one with 7% fewer chunks.
    #
    #    If yield matters more than latency for some future corpus, revert this
    #    default rather than tuning around it — and consider spending the 5x on a
    #    second `structured` pass instead, which would still beat `class-30b` on
    #    wall clock. Re-measure with:
    #      uv run python scripts/eval_chunker_models.py --models structured class-30b
    #
    # c) Tenancy, which the 2026-08-18 note gave as the other reason to split.
    #    `structured` is also Listings AI's extraction role (LLM_MODEL_EXTRACT in
    #    its provider.ts), so ingestion shares a runner with it again. Accepted
    #    deliberately: Listings AI is experimental and lower priority than
    #    ingestion, and it queues. The split did not really buy isolation anyway —
    #    ollama had `class-30b` at `-np 1`, so it serialised everything regardless.
    #
    #    A dedicated runner is NOT available by tagging. Two tags on the same
    #    weights cannot coexist: ollama evicts one (verified 2026-08-20 with only
    #    two models resident, so it is not OLLAMA_MAX_LOADED_MODELS). A second
    #    `ollama serve` instance does work and yields two runners on one blob, but
    #    costs a full ~28.9GB weight copy for queue isolation only — no throughput
    #    gain, since one GPU means instances split bandwidth.
    #
    # Slot grants are decided at MODEL LOAD time against free memory and ollama is
    # silent when it declines, so a model can sit at one slot for a whole run by
    # accident. `OLLAMA_CONTEXT_LENGTH=16384` in ~/Library/LaunchAgents/
    # com.ollama.server.plist keeps the KV estimate small enough that `-np 4` is
    # granted reliably. Never trust the env var — check the real value with:
    #   pgrep -fl llama-server   # look for the actual -np on the loaded blob
    #
    # `structured` is a ROLE ALIAS (~/code/ollama/aliases/structured.Modelfile,
    # `make repoint NAME=structured MODEL=...`). Address the alias, never the raw
    # `gemma4:26b-a4b-it-q8_0` tag — calling both loads the same weights twice.
    #
    # Not pinned in ~/.ollama/warmup.sh, so the first chunker call of a run pays a
    # ~29GB cold load. That is now paid on every run rather than occasionally, but
    # it is one load amortised over hours of batch work.
    llm_chunker_model: str = os.getenv("LLM_CHUNKER_MODEL", "structured")

    # Per-request LLM timeout. This has to cover *queue* time, not just generation:
    # max_concurrent_files (10) exceeds the server's per-model concurrency
    # (OLLAMA_NUM_PARALLEL=4), so requests 5-10 of a batch sit in the server queue
    # before a slot frees. The old 60s budget timed those out and tripped the
    # circuit breaker while the server was healthy and simply busy. There is RAM
    # headroom for the queue to drain, so wait for it.
    #
    # Raised 300 -> 900 on 2026-08-18 alongside the chunker move to `class-30b`.
    # That model averages ~41s/call against `structured`'s ~12s, which cuts the
    # margin over queue time from roughly 7x to ~2x — and measured wall clock for
    # one 20-file arm varied 656s to 1116s on identical work, so the variance is
    # real and eats margin. 300s would have re-created the exact failure the
    # paragraph above describes.
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "900"))

    # Incremental Update Configuration
    enable_incremental_updates: bool = os.getenv("ENABLE_INCREMENTAL_UPDATES", "false").lower() == "true"

    # Async Pipeline Configuration
    max_concurrent_files: int = int(os.getenv("MAX_CONCURRENT_FILES", "10"))  # Process N files at once
    max_parsing_threads: int = int(os.getenv("MAX_PARSING_THREADS", "4"))     # Thread pool for CPU-bound parsing
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "128")) # Chunks per embedding batch

    # GitHub Configuration
    github_token: str = os.getenv("GITHUB_TOKEN", "").strip()
    github_repos: str = os.getenv("GITHUB_REPOS", "")

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Chief of Staff (cos) API — daily digest sink.
    # cos_token is a PAT (prefix "cos_pat_"). The chief-of-staff repo's
    # COS_SERVICE_TOKEN can be reused here. cos_api_url is the cos service URL
    # (different from CODESMRITI_API_URL which points to code-smriti's own API).
    cos_api_url: str = os.getenv("COS_API_URL", "http://localhost:8001").rstrip("/")
    cos_token: str = os.getenv("COS_TOKEN", "")
    # Default to the "Chief of Staff" project UUID; override per deployment.
    cos_digest_project_id: str = os.getenv("COS_DIGEST_PROJECT_ID", "7e3aaaab-5b4c-43d9-ac52-2bcb88c8bd49")

    # Parsing Configuration
    supported_code_extensions: list = [
        ".py", ".js", ".ts", ".tsx", ".jsx",  # Python, JavaScript, TypeScript
        ".svelte", ".vue",                     # Component frameworks
        ".html", ".htm",                       # HTML
        ".css", ".scss", ".sass",              # Stylesheets
        ".sql",                                # SQL
        ".java", ".kt",                        # JVM
        ".swift",                              # iOS / macOS
        ".erl", ".hrl", ".ex", ".exs",         # BEAM
        ".rs",                                 # Rust
        ".sh", ".bash", ".zsh",                # Shell
    ]
    supported_doc_extensions: list = [
        ".md", ".rst",                    # Markdown, reStructuredText (Sphinx)
        ".txt",                           # Plain text
        ".json", ".yaml", ".yml",         # Configuration/data
        ".toml", ".ini", ".cfg"           # Config files
    ]
    max_chunk_size: int = 2000  # characters
    min_chunk_size: int = 50  # characters

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = 'ignore'  # Ignore extra env vars (e.g., MCP server configs)
