#!/usr/bin/env python3
"""
Eval: Gemma 4 31B vs stored Nemotron BDR baseline.

For each of 4 chosen repos, pulls the same inputs the BDR pipeline uses,
runs Gemma 4 31B in two modes (thinking off, thinking default), and writes
side-by-side comparison files alongside the stored nemotron baseline.

Outputs land under: scripts/eval_gemma_bdr_results/<repo_safe>/
  - inputs.md                  (the prompt inputs — identical across runs)
  - baseline_nemotron.md       (BDR currently stored in code_kosha)
  - gemma_no_thinking.md       (reasoning.effort = "none")
  - gemma_thinking.md          (reasoning omitted = default on)
  - timing.json                (latency + token counts per run)
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, Optional

import httpx
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import WorkerConfig
from storage.couchbase_client import CouchbaseClient


# v2 prompt: closes the three regressions found vs nemotron in v1 eval
# (sparse keyword triggers, generic competitor naming, missing TL;DR closer).
BDR_PROMPT = """You are helping a Business Development Representative understand
what business value this codebase represents. Translate technical capabilities
into business intelligence that helps match prospects to solutions.

## Repository: {repo_id}

## Technical Summary:
{repo_summary}

## README:
{readme_content}

## Module Summaries:
{module_summaries}

---

Analyze this repository and generate a BDR brief:

### BUSINESS VALUE
What business outcome does this enable? Focus on ROI, efficiency, risk reduction,
or competitive advantage — not technical features.

### TARGET PROSPECTS
Who specifically would need this? Be concrete about:
- Industry/segment
- Role/title
- Company type

### PAIN POINTS ADDRESSED
What problems are these prospects experiencing that this solves?
Write as the prospect would describe it (not technical jargon).

### DISCOVERY QUESTIONS
What should the BDR ask to qualify if this is a fit?
5-7 questions that reveal whether the prospect has the problem this solves.

### PROSPECT SIGNALS
How would a prospect describe this need? Write 5-10 ways they might phrase it.

### KEYWORD TRIGGERS
Render as a 4-column markdown table with at least 5 entries per column.
Columns: **Business Terms** | **Technical Terms** | **Acronyms / Expansions** | **Adjacent Concepts**.
- Business Terms: what prospects say in their own language
- Technical Terms: what their engineers would say
- Acronyms / Expansions: include the expansion in parentheses (e.g., "RBAC (Role-Based Access Control)")
- Adjacent Concepts: related domains or capabilities a buyer might also be exploring

### NOT A FIT
When should the BDR disqualify? What problems does this NOT solve?

### ADJACENT OPPORTUNITIES
If a prospect needs this, what else might they need?

### COMPETITIVE CONTEXT
Name specific real-world alternatives — actual product names, not generic categories.
For each, give a one-line differentiator explaining how this offering is different.
Format as a table with columns: **Alternative** | **Differentiator**.
If you genuinely cannot identify named competitors from public knowledge, say
"requires market research" — but prefer naming products when possible.

### BOTTOM LINE FOR THE BDR
A short closing paragraph (3-5 sentences) that wraps the brief into a usable
summary: when to engage, what to listen for, and the single sharpest one-liner
positioning statement the BDR can repeat in a discovery call.
"""


REPOS = [
    "kbhalerao/agkit.io",
    "kbhalerao/agkit.io-x402",
    "kbhalerao/labcore-mobile-sdk",
    "jayp-eci/labcore",
]

# (model_id, output_label, thinking) triples to run per repo. Skip-if-exists makes this idempotent.
MODELS = [
    ("google/gemma-4-31b", "gemma31b_thinking_v2", True),
    ("google/gemma-4-31b", "gemma31b_no_thinking_v2", False),
    ("google/gemma-4-e2b", "gemma_e2b_thinking_v2", True),
    ("google/gemma-4-26b-a4b", "gemma_26b_a4b_thinking_v2", True),
    ("google/gemma-4-26b-a4b", "gemma_26b_a4b_no_thinking_v2", False),
    ("google/gemma-4-e4b", "gemma_e4b_thinking_v2", True),
    ("google/gemma-4-e4b", "gemma_e4b_no_thinking_v2", False),
]
LMSTUDIO_URL = "http://localhost:1234"
TEMPERATURE = 0.3
MAX_OUTPUT_TOKENS = 16000
TIMEOUT_SECONDS = 900.0  # Gemma dense Q8 is slow; thinking mode can take a while.

OUT_DIR = Path(__file__).parent / "eval_gemma_bdr_results"


def safe_name(repo_id: str) -> str:
    return repo_id.replace("/", "__")


def load_inputs(cb: CouchbaseClient, cfg: WorkerConfig, repo_id: str) -> Dict[str, str]:
    """Pull the same inputs generate_bdr.py uses: repo summary, modules, README."""
    summ = list(cb.cluster.query(
        'SELECT content, commit_hash FROM `code_kosha` '
        'WHERE type="repo_summary" AND repo_id=$repo_id LIMIT 1',
        repo_id=repo_id,
    ))
    if not summ:
        raise RuntimeError(f"No repo_summary for {repo_id}")
    repo_summary = summ[0]["content"]
    commit_hash = summ[0].get("commit_hash", "")

    mod_rows = list(cb.cluster.query(
        'SELECT module_path, content FROM `code_kosha` '
        'WHERE type="module_summary" AND repo_id=$repo_id AND content IS NOT NULL '
        'ORDER BY module_path LIMIT 20',
        repo_id=repo_id,
    ))
    module_summaries = "\n\n".join(
        f"### {r['module_path']}/\n{r['content']}" for r in mod_rows
    )

    owner, name = repo_id.split("/")
    readme = ""
    repo_path = Path(cfg.repos_path) / owner / name
    for fname in ["README.md", "readme.md", "README.rst", "README.txt", "README"]:
        p = repo_path / fname
        if p.exists():
            try:
                readme = p.read_text(encoding="utf-8", errors="ignore")[:5000]
                break
            except Exception as e:
                logger.warning(f"Failed to read {p}: {e}")

    return {
        "repo_summary": repo_summary,
        "readme_content": readme or "(No README available)",
        "module_summaries": module_summaries or "(No module summaries available)",
        "commit_hash": commit_hash,
    }


def load_baseline(cb: CouchbaseClient, repo_id: str) -> Optional[Dict]:
    """Pull stored BDR for repo (the nemotron baseline)."""
    rows = list(cb.cluster.query(
        'SELECT content, reasoning_trace, metadata, last_checked, source_commit '
        'FROM `code_kosha` WHERE type="repo_bdr" AND repo_id=$repo_id LIMIT 1',
        repo_id=repo_id,
    ))
    return rows[0] if rows else None


async def call_gemma(prompt: str, thinking: bool, model: str) -> Dict:
    """Call a Gemma model via /v1/responses. Returns dict with output, reasoning, usage, latency_s."""
    payload = {
        "model": model,
        "input": prompt,
        "temperature": TEMPERATURE,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    if not thinking:
        payload["reasoning"] = {"effort": "none"}

    started = time.monotonic()
    async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
        resp = await client.post(f"{LMSTUDIO_URL}/v1/responses", json=payload)
        resp.raise_for_status()
        data = resp.json()
    latency_s = time.monotonic() - started

    reasoning_text = None
    output_text = None
    for item in data.get("output", []):
        if item.get("type") == "reasoning":
            for block in item.get("content", []):
                if block.get("type") == "reasoning_text":
                    reasoning_text = block.get("text", "")
                    break
        elif item.get("type") == "message":
            for block in item.get("content", []):
                if block.get("type") == "output_text":
                    output_text = block.get("text", "")
                    break

    if output_text is None:
        raise RuntimeError(f"No output text in response: {json.dumps(data)[:500]}")

    return {
        "output": output_text,
        "reasoning": reasoning_text,
        "usage": data.get("usage", {}),
        "latency_s": latency_s,
        "status": data.get("status"),
    }


def write_run(repo_dir: Path, label: str, result: Dict, header: str):
    out = repo_dir / f"{label}.md"
    parts = [f"# {header}\n"]
    usage = result.get("usage", {})
    parts.append(f"- latency: {result.get('latency_s', 0):.1f}s")
    parts.append(f"- input_tokens: {usage.get('input_tokens')}")
    parts.append(f"- output_tokens: {usage.get('output_tokens')}")
    rt = usage.get("output_tokens_details", {}).get("reasoning_tokens")
    if rt is not None:
        parts.append(f"- reasoning_tokens: {rt}")
    parts.append("")
    if result.get("reasoning"):
        parts.append("## Reasoning trace\n")
        parts.append("```")
        parts.append(result["reasoning"])
        parts.append("```\n")
    parts.append("## Output\n")
    parts.append(result["output"])
    out.write_text("\n".join(parts))


def prep_repo(cb: CouchbaseClient, cfg: WorkerConfig, repo_id: str) -> Dict:
    """Build prompt, write inputs.md + baseline_nemotron.md, return per-repo state."""
    logger.info(f"=== prep {repo_id} ===")
    repo_dir = OUT_DIR / safe_name(repo_id)
    repo_dir.mkdir(parents=True, exist_ok=True)

    inputs = load_inputs(cb, cfg, repo_id)
    prompt = BDR_PROMPT.format(repo_id=repo_id, **{k: v for k, v in inputs.items() if k != "commit_hash"})

    (repo_dir / "inputs.md").write_text(
        f"# Inputs for {repo_id}\n\n"
        f"- commit_hash: {inputs['commit_hash']}\n"
        f"- prompt_chars: {len(prompt)}\n\n"
        f"## Repo summary\n\n{inputs['repo_summary']}\n\n"
        f"## README (first 5000 chars)\n\n{inputs['readme_content']}\n\n"
        f"## Module summaries\n\n{inputs['module_summaries']}\n"
    )

    baseline = load_baseline(cb, repo_id)
    if baseline:
        meta = baseline.get("metadata") or {}
        parts = [
            f"# Baseline (stored): {repo_id}",
            f"- model: {meta.get('model')}",
            f"- generation_tokens: {meta.get('generation_tokens')}",
            f"- reasoning_tokens: {meta.get('reasoning_tokens')}",
            f"- last_checked: {baseline.get('last_checked')}",
            f"- source_commit: {baseline.get('source_commit')}",
            "",
        ]
        if baseline.get("reasoning_trace"):
            parts.extend(["## Reasoning trace\n", "```", baseline["reasoning_trace"], "```\n"])
        parts.extend(["## Output\n", baseline["content"]])
        (repo_dir / "baseline_nemotron.md").write_text("\n".join(parts))

    return {"repo_id": repo_id, "repo_dir": repo_dir, "prompt": prompt}


def load_timing(repo_dir: Path, repo_id: str, prompt_chars: int) -> Dict:
    timing_path = repo_dir / "timing.json"
    if timing_path.exists():
        timing = json.loads(timing_path.read_text())
        timing.setdefault("runs", {})
    else:
        timing = {"repo_id": repo_id, "prompt_chars": prompt_chars, "runs": {}}
    return timing


async def run_model_on_repo(state: Dict, model_id: str, label: str, thinking: bool) -> None:
    repo_dir = state["repo_dir"]
    repo_id = state["repo_id"]
    prompt = state["prompt"]

    out_path = repo_dir / f"{label}.md"
    if out_path.exists():
        logger.info(f"  [{repo_id}] {model_id} → {label}: already exists, skip")
        return

    timing = load_timing(repo_dir, repo_id, len(prompt))
    mode = "thinking" if thinking else "no thinking"
    logger.info(f"  [{repo_id}] {model_id} → {label} ({mode}) ...")
    try:
        r = await call_gemma(prompt, thinking=thinking, model=model_id)
    except Exception as e:
        logger.error(f"    {model_id} failed: {e}")
        timing["runs"][label] = {"error": str(e)}
        (repo_dir / "timing.json").write_text(json.dumps(timing, indent=2))
        return
    write_run(repo_dir, label, r, f"{model_id} ({mode}, v2 prompt) — {repo_id}")
    timing["runs"][label] = {
        "latency_s": r["latency_s"],
        "usage": r["usage"],
        "output_chars": len(r["output"]),
    }
    (repo_dir / "timing.json").write_text(json.dumps(timing, indent=2))
    logger.info(f"    {r['latency_s']:.1f}s, {r['usage'].get('output_tokens')} out tok, {len(r['output'])} chars")


async def main():
    OUT_DIR.mkdir(exist_ok=True)
    cb = CouchbaseClient()
    cfg = WorkerConfig()

    # Prep every repo first (cheap: just Couchbase + filesystem writes).
    repo_states: Dict[str, Dict] = {}
    for repo in REPOS:
        try:
            repo_states[repo] = prep_repo(cb, cfg, repo)
        except Exception as e:
            logger.exception(f"prep failed for {repo}: {e}")

    # Model-outer / repo-inner: each model loads at most once across the whole run.
    for model_id, label, thinking in MODELS:
        logger.info(f"\n>>> model: {model_id} → {label}")
        for repo in REPOS:
            state = repo_states.get(repo)
            if state is None:
                continue
            await run_model_on_repo(state, model_id, label, thinking)

    # Aggregate timings across repos for convenience.
    all_timings = []
    for repo in REPOS:
        repo_dir = OUT_DIR / safe_name(repo)
        timing_path = repo_dir / "timing.json"
        if timing_path.exists():
            all_timings.append(json.loads(timing_path.read_text()))
    (OUT_DIR / "_all_timings.json").write_text(json.dumps(all_timings, indent=2))
    logger.info(f"\nResults: {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
