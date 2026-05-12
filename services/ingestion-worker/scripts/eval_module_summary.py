#!/usr/bin/env python3
"""
Eval: a4b (no thinking) vs qwen3-30b-a3b (current default) for module summary generation.

Module summaries are shorter than BDR briefs (2-4 sentences) and lower-stakes,
but the production pipeline runs thousands of them. This eval confirms a4b parity
before we flip the enricher default in LMSTUDIO_CONFIG.

For each (repo, module) selected, reconstructs the exact files_context the v4
aggregator builds (file_summary + child module_summary contents, "\\n\\n---\\n\\n"
join, [:15]), runs both models against the same enrich_module prompt, and saves:
  - inputs.md          (files_context + prompt)
  - baseline.md        (current stored module_summary content)
  - a4b.md             (gemma-4-26b-a4b, reasoning.effort=none)
  - qwen30.md          (qwen/qwen3-30b-a3b-2507, default thinking)
  - timing.json        (latency + token counts per run)
"""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from storage.couchbase_client import CouchbaseClient


# (repo_id, module_path) targets. Mix of leaf modules and aggregating parents,
# spanning the same 4 repos used in the BDR eval for continuity.
TARGETS: List[Tuple[str, str]] = [
    ("kbhalerao/agkit.io", "src/routes"),
    ("kbhalerao/agkit.io", "src/lib/components/ui"),
    ("kbhalerao/agkit.io-x402", "apps/adapter"),
    ("kbhalerao/labcore-mobile-sdk", "Labcore"),
    ("jayp-eci/labcore", "associates"),
    ("jayp-eci/labcore", "bugs"),
]

MODELS = [
    ("google/gemma-4-26b-a4b", "a4b", {"effort": "none"}),
    # qwen3-30b-a3b-2507 was the original comparator but its LM Studio preset
    # references a missing 0.5B draft model and 500s on every call; swapped to
    # qwen3-next-80b which is loaded and unconfigured for speculative decoding.
    ("qwen/qwen3-next-80b", "qwen80", None),  # default thinking behavior
]

LMSTUDIO_URL = "http://localhost:1234"
TEMPERATURE = 0.3
MAX_OUTPUT_TOKENS = 2000
TIMEOUT_SECONDS = 300.0

OUT_DIR = Path(__file__).parent / "eval_module_summary_results"


def safe_name(repo_id: str, module_path: str) -> str:
    return f"{repo_id.replace('/', '__')}__{module_path.replace('/', '_') or 'root'}"


def _is_direct_child(file_path: str, module_path: str) -> bool:
    """True when file_path is directly inside module_path (no nested subdir)."""
    if not module_path:
        return "/" not in file_path
    if not file_path.startswith(f"{module_path}/"):
        return False
    return "/" not in file_path[len(module_path) + 1 :]


def _is_direct_child_module(child: str, module_path: str) -> bool:
    if not module_path:
        return "/" not in child and child != ""
    if not child.startswith(f"{module_path}/"):
        return False
    return "/" not in child[len(module_path) + 1 :]


def gather_context(cb: CouchbaseClient, repo_id: str, module_path: str) -> Tuple[List[str], Optional[Dict]]:
    """Pull file + child-module summary contents for the target module.

    Mirrors v4/aggregator.py: file_summaries (content of file_index docs in this
    module, direct children only) + nested_summaries (content of module_summary
    docs whose module_path is a direct child of this one), joined later by caller.
    """
    file_rows = list(cb.cluster.query(
        'SELECT content, file_path FROM `code_kosha` '
        'WHERE type="file_index" AND repo_id=$repo_id AND content IS NOT NULL '
        'ORDER BY file_path',
        repo_id=repo_id,
    ))
    file_summaries = [
        r["content"] for r in file_rows
        if _is_direct_child(r.get("file_path", ""), module_path)
    ]

    mod_rows = list(cb.cluster.query(
        'SELECT content, module_path FROM `code_kosha` '
        'WHERE type="module_summary" AND repo_id=$repo_id AND content IS NOT NULL',
        repo_id=repo_id,
    ))
    child_module_summaries = [
        r["content"] for r in mod_rows
        if _is_direct_child_module(r.get("module_path", ""), module_path)
    ]

    baseline_rows = list(cb.cluster.query(
        'SELECT content, file_count FROM `code_kosha` '
        'WHERE type="module_summary" AND repo_id=$repo_id AND module_path=$mp '
        'LIMIT 1',
        repo_id=repo_id,
        mp=module_path,
    ))
    baseline = baseline_rows[0] if baseline_rows else None

    return file_summaries + child_module_summaries, baseline


def build_prompt(repo_id: str, module_path: str, summaries: List[str]) -> str:
    """Mirror v4/llm_enricher.py:enrich_module prompt exactly."""
    files_context = "\n\n---\n\n".join(summaries[:15])
    return f"""Summarize this code module based on its files.

Repository: {repo_id}
Module: {module_path}/

File summaries:
{files_context[:6000]}

Write a 2-4 sentence summary explaining:
1. What this module/package does
2. Its key components
3. How other code would use it

Be concise. Focus on the module's role in the codebase."""


async def call_model(prompt: str, model: str, reasoning: Optional[Dict]) -> Dict:
    payload = {
        "model": model,
        "input": prompt,
        "temperature": TEMPERATURE,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
    }
    if reasoning is not None:
        payload["reasoning"] = reasoning

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
    }


def write_run(target_dir: Path, label: str, result: Dict, header: str) -> None:
    out = target_dir / f"{label}.md"
    usage = result.get("usage", {})
    parts = [
        f"# {header}",
        f"- latency: {result['latency_s']:.1f}s",
        f"- input_tokens: {usage.get('input_tokens')}",
        f"- output_tokens: {usage.get('output_tokens')}",
    ]
    rt = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
    if rt is not None:
        parts.append(f"- reasoning_tokens: {rt}")
    parts.append("")
    if result.get("reasoning"):
        parts.extend(["## Reasoning trace\n", "```", result["reasoning"], "```", ""])
    parts.extend(["## Output\n", result["output"]])
    out.write_text("\n".join(parts))


async def main():
    OUT_DIR.mkdir(exist_ok=True)
    cb = CouchbaseClient()

    # Prep every target first (cheap: Couchbase + file writes).
    states = []
    for repo_id, module_path in TARGETS:
        target_dir = OUT_DIR / safe_name(repo_id, module_path)
        target_dir.mkdir(parents=True, exist_ok=True)
        summaries, baseline = gather_context(cb, repo_id, module_path)
        if not summaries:
            logger.warning(f"[{repo_id}/{module_path}] no inputs — skip")
            continue
        prompt = build_prompt(repo_id, module_path, summaries)
        (target_dir / "inputs.md").write_text(
            f"# Inputs for {repo_id}/{module_path}\n\n"
            f"- input_summaries: {len(summaries)} (capped to 15)\n"
            f"- prompt_chars: {len(prompt)}\n\n"
            f"## Prompt\n\n```\n{prompt}\n```\n"
        )
        if baseline:
            (target_dir / "baseline.md").write_text(
                f"# Baseline (stored): {repo_id}/{module_path}\n\n"
                f"- file_count: {baseline.get('file_count')}\n\n"
                f"## Output\n\n{baseline['content']}\n"
            )
        states.append({
            "repo_id": repo_id, "module_path": module_path,
            "target_dir": target_dir, "prompt": prompt,
            "input_summaries": len(summaries),
        })

    all_timings: Dict[str, Dict] = {}
    for model_id, label, reasoning in MODELS:
        logger.info(f"\n>>> model: {model_id} → {label}")
        for st in states:
            key = f"{st['repo_id']}/{st['module_path']}"
            all_timings.setdefault(key, {
                "repo_id": st["repo_id"], "module_path": st["module_path"],
                "input_summaries": st["input_summaries"], "runs": {}
            })
            out_path = st["target_dir"] / f"{label}.md"
            if out_path.exists():
                logger.info(f"  [{key}] {model_id} → {label}: skip (exists)")
                continue
            mode = "no thinking" if reasoning and reasoning.get("effort") == "none" else "thinking"
            logger.info(f"  [{key}] {model_id} → {label} ({mode})...")
            try:
                r = await call_model(st["prompt"], model_id, reasoning)
            except Exception as e:
                logger.error(f"    {model_id} failed: {e}")
                all_timings[key]["runs"][label] = {"error": str(e)}
                continue
            write_run(st["target_dir"], label, r, f"{model_id} ({mode}) — {key}")
            all_timings[key]["runs"][label] = {
                "latency_s": r["latency_s"],
                "usage": r["usage"],
                "output_chars": len(r["output"]),
            }
            (st["target_dir"] / "timing.json").write_text(json.dumps(all_timings[key], indent=2))
            logger.info(f"    {r['latency_s']:.1f}s, {r['usage'].get('output_tokens')} out tok, {len(r['output'])} chars")

    (OUT_DIR / "_all_timings.json").write_text(json.dumps(list(all_timings.values()), indent=2))
    logger.info(f"\nResults: {OUT_DIR}")


if __name__ == "__main__":
    asyncio.run(main())
