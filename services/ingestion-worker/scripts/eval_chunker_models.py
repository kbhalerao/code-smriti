#!/usr/bin/env python3
"""Compare chunker models on extraction yield under schema-constrained decoding.

Reproduces the experiment behind the yield table in `config.llm_chunker_model`
(20 files x 3 enrichment passes, chunks kept) so a candidate model gets a number
directly comparable to the recorded baselines rather than an impression.

Yield is the metric because schema *compliance* is not discriminating: ollama
enforces `json_schema` on every GGUF build, so any GGUF candidate emits valid
JSON. What varies is how much a model finds while constrained. The columns that
are not `kept` exist to catch a model that scores well by cheating — returning
valid-but-empty arrays, or getting truncated mid-document and silently reported
as `completed`.

Usage:
    uv run python scripts/eval_chunker_models.py
    uv run python scripts/eval_chunker_models.py --models structured granite4.1:3b-q8_0
    uv run python scripts/eval_chunker_models.py --files 20 --seed 0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from config import WorkerConfig
from llm_chunker import ENRICHMENT_PASSES, LLMChunker

config = WorkerConfig()

# Baselines from the recorded experiment. Same 20x3 *shape*, but the original
# file sample was not preserved, so these are NOT directly comparable to a run
# here — a richer or barer sample moves every arm together. They are printed for
# rough scale only. The real baseline for any run is its own `structured` arm,
# which is scored on the identical seeded sample as every candidate.
KNOWN_BASELINES = {
    "gemma4 Q4_0 GGUF + schema": 99,
    "gemma4 Q4_0 GGUF, no schema": 117,
    "gemma4 Q8_0 GGUF + schema (structured)": 130,
    "gemma4 nvfp4 MLX, schema ignored": 142,
}

# Only these get all three enrichment passes (embedded_code and api_contracts
# are gated to python/js/ts), so restricting the sample keeps the per-file
# workload identical across models and comparable to the baseline.
SAMPLE_SUFFIXES = {".py"}
MIN_BYTES = 1_500
MAX_BYTES = 60_000


@dataclass
class ModelResult:
    model: str
    fmt: str = "?"
    kept: int = 0
    raw: int = 0
    parse_failures: int = 0
    wrong_shape: int = 0
    fenced: int = 0
    truncated: int = 0
    empty: int = 0
    calls: int = 0
    errors: int = 0
    seconds: float = 0.0
    per_file: dict = field(default_factory=dict)

    @property
    def clean(self) -> int:
        return self.kept - 0  # kept already excludes unparsed; kept for symmetry


def pick_files(repos_root: Path, n: int, seed: int) -> list[Path]:
    """Deterministic sample of real source files.

    Sorted-then-seeded rather than os.walk order so the same seed selects the
    same files across runs and machines — otherwise two models are scored on
    different inputs and the comparison means nothing.
    """
    candidates: list[Path] = []
    for repo in sorted(p for p in repos_root.iterdir() if p.is_dir()):
        for f in sorted(repo.rglob("*.py")):
            parts = set(f.parts)
            if parts & {".git", "node_modules", ".venv", "venv", "__pycache__", "site-packages"}:
                continue
            try:
                size = f.stat().st_size
            except OSError:
                continue
            if MIN_BYTES <= size <= MAX_BYTES:
                candidates.append(f)
    rng = random.Random(seed)
    rng.shuffle(candidates)
    return candidates[:n]


async def model_format(model: str) -> str:
    """Ask ollama which engine will serve this tag.

    A `safetensors` answer invalidates the whole run: the MLX runner accepts a
    json_schema and ignores it, so the model is scored unconstrained while every
    other arm is constrained.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{config.llm_base_url}/api/show", json={"model": model})
            r.raise_for_status()
            return r.json().get("details", {}).get("format", "?")
    except Exception as e:
        return f"?({type(e).__name__})"


class ResponseTap:
    """Captures every raw model response so the harness can classify failures itself.

    The chunker swallows a bad response by design (logs, returns []), which is
    right for ingestion and useless for evaluation — an empty array and a parse
    failure are the same zero downstream. This distinguishes them.
    """

    def __init__(self, chunker: LLMChunker, result: ModelResult):
        self.chunker = chunker
        self.result = result
        self._inner = chunker._call_llm

    def install(self) -> None:
        async def tapped(prompt: str, schema: dict = None) -> str:
            self.result.calls += 1
            try:
                resp = await self._inner(prompt, schema=schema)
            except Exception:
                self.result.errors += 1
                raise
            self._classify(resp)
            return resp
        self.chunker._call_llm = tapped

    def _classify(self, resp: str) -> None:
        r = self.result
        text = (resp or "").strip()
        if not text:
            r.empty += 1
            return
        if "```" in text:
            # Constrained decoding cannot emit a fence. Its presence means the
            # schema did not bind for this call.
            r.fenced += 1
        stripped = text
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.startswith("json"):
                stripped = stripped[4:]
        try:
            parsed = json.loads(stripped.strip())
        except json.JSONDecodeError:
            r.parse_failures += 1
            # An unterminated document is the truncation signature; the server
            # reports status "completed" either way.
            if not text.rstrip().endswith(("]", "}")):
                r.truncated += 1
            return
        if not isinstance(parsed, list):
            r.wrong_shape += 1
            return
        r.raw += len(parsed)
        if not parsed:
            r.empty += 1


async def run_model(model: str, files: list[Path], repos_root: Path) -> ModelResult:
    result = ModelResult(model=model)
    result.fmt = await model_format(model)
    if result.fmt != "gguf":
        print(f"  !! format={result.fmt} — schema will NOT be enforced; numbers are not comparable")

    chunker = LLMChunker(model=model)
    ResponseTap(chunker, result).install()

    t0 = time.perf_counter()
    for i, path in enumerate(files, 1):
        try:
            content = path.read_text(errors="replace")
        except OSError:
            continue
        try:
            chunks = await chunker.analyze_file(
                file_path=str(path),
                content=content,
                language="python",
                existing_chunks=[],
                passes=ENRICHMENT_PASSES,
            )
        except Exception as e:
            print(f"  [{i}/{len(files)}] {path.name}: ERROR {type(e).__name__}: {e}")
            continue
        result.kept += len(chunks)
        result.per_file[str(path.relative_to(repos_root))] = len(chunks)
        print(f"  [{i}/{len(files)}] {path.name}: {len(chunks)} kept (running {result.kept})")
    result.seconds = time.perf_counter() - t0
    await chunker.close()
    return result


def print_table(results: list[ModelResult], n_files: int) -> None:
    print("\n" + "=" * 100)
    print(f"CHUNKER MODEL COMPARISON — {n_files} files x {len(ENRICHMENT_PASSES)} passes")
    print("=" * 100)
    hdr = f"{'model':<28} {'fmt':<6} {'kept':>6} {'raw':>6} {'parse!':>7} {'shape!':>7} {'fence':>6} {'trunc':>6} {'empty':>6} {'sec':>8}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        print(
            f"{r.model:<28} {r.fmt:<6} {r.kept:>6} {r.raw:>6} {r.parse_failures:>7} "
            f"{r.wrong_shape:>7} {r.fenced:>6} {r.truncated:>6} {r.empty:>6} {r.seconds:>8.1f}"
        )
    print("\nRecorded baselines — DIFFERENT file sample, scale reference only:")
    for label, val in KNOWN_BASELINES.items():
        print(f"  {val:>4}  {label}")
    print("\nkept   = chunks passing the confidence>=0.7 filter (the headline metric)")
    print("raw    = items returned before that filter")
    print("parse! = responses that were not valid JSON; shape! = valid JSON but not a list")
    print("fence  = markdown fence present, i.e. the schema did not bind on that call")
    print("trunc  = parse failure with an unterminated document (max_output_tokens)")
    print("\nCompare candidates against the `structured` row above, not the recorded")
    print("baselines — only the rows in this table share a file sample.")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+",
                    default=["structured", "granite4.1:3b-q8_0", "lfm2.5:8b-a1b-q8_0"])
    ap.add_argument("--files", type=int, default=20)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default="scripts/eval_chunker_models_results/results.json")
    args = ap.parse_args()

    logging.basicConfig(level=logging.ERROR)

    repos_root = Path(config.repos_path)
    if not repos_root.is_dir():
        sys.exit(f"REPOS_PATH not a directory: {repos_root}")

    files = pick_files(repos_root, args.files, args.seed)
    print(f"Sampled {len(files)} files from {repos_root} (seed={args.seed})")
    if len(files) < args.files:
        print(f"  !! wanted {args.files}, found {len(files)}")

    results = []
    for model in args.models:
        print(f"\n--- {model} ---")
        results.append(await run_model(model, files, repos_root))

    print_table(results, len(files))

    out = Path(__file__).parent.parent / args.out
    out.write_text(json.dumps(
        {
            "seed": args.seed,
            "files": [str(f.relative_to(repos_root)) for f in files],
            "passes": [p.name for p in ENRICHMENT_PASSES],
            "results": [
                {k: v for k, v in vars(r).items()} for r in results
            ],
        },
        indent=2,
    ))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
