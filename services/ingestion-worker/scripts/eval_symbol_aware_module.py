#!/usr/bin/env python3
"""
Eval: prose-only vs symbol-aware module context, model held fixed.

The question this answers is whether module summary quality is limited by model
strength or by what the module prompt is given. It therefore holds the model
constant (whatever LLM_MODEL resolves to) and varies only the context builder:

  baseline  — what is stored in Couchbase today
  old       — current pipeline: file .content joined, [:15] files, [:6000] chars
  new       — v4.module_context.build_module_context: per-file symbols, real
              docstrings, imports, no tail truncation

Usage:
    uv run python scripts/eval_symbol_aware_module.py
    uv run python scripts/eval_symbol_aware_module.py --repo kbhalerao/agkit.io-backend \
        --module tier1apps/gislayers

Writes per-target markdown to scripts/eval_symbol_aware_results/.
"""

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from storage.couchbase_client import CouchbaseClient
from v4.schemas import FileIndex, ModuleSummary
from v4.doc_loader import (
    load_repo_file_docs,
    load_repo_module_docs,
    resolve_module_children,
)
from v4.module_context import build_module_context
from v4.llm_enricher import V4LLMEnricher
from llm_enricher import LLM_CONFIG

DEFAULT_TARGETS: List[Tuple[str, str]] = [
    ("kbhalerao/agkit.io-backend", "tier1apps/gislayers"),
]

OUT_DIR = Path(__file__).parent / "eval_symbol_aware_results"


def gather(cb: CouchbaseClient, repo_id: str, module_path: str):
    """Pull the file and child-module docs the aggregator would see."""
    file_docs = load_repo_file_docs(cb.cluster, repo_id)
    module_docs = load_repo_module_docs(cb.cluster, repo_id)
    file_indices, children = resolve_module_children(file_docs, module_docs, module_path)
    baseline = next(
        (d.get("content") for d in module_docs if d.get("module_path") == module_path),
        None,
    )
    return file_indices, children, baseline


def build_old_context(file_indices: List[FileIndex], children: List[ModuleSummary]) -> str:
    """Reproduce v4/aggregator.py + v4/llm_enricher.py:enrich_module exactly."""
    summaries = (
        [f.content for f in file_indices if f.content]
        + [m.content for m in children if m.content]
    )
    return "\n\n---\n\n".join(summaries[:15])[:6000]


async def run_one(
    enricher: V4LLMEnricher,
    repo_id: str,
    module_path: str,
    file_indices: List[FileIndex],
    children: List[ModuleSummary],
    baseline: Optional[str],
) -> Dict:
    old_ctx = build_old_context(file_indices, children)
    new_ctx = build_module_context(file_indices, children)

    n_inputs = len(file_indices) + len(children)
    logger.info(
        f"{repo_id}:{module_path} — {len(file_indices)} files + {len(children)} submodules; "
        f"old context {len(old_ctx)} chars, new context {len(new_ctx)} chars"
    )

    results = {}
    for label, ctx, use_new in (("old", old_ctx, False), ("new", new_ctx, True)):
        t0 = time.time()
        if use_new:
            out = await enricher.enrich_module_symbol_aware(module_path, ctx, repo_id)
        else:
            out = await enricher.enrich_module(module_path, ctx, repo_id)
        results[label] = {
            "summary": out["summary"],
            "seconds": round(time.time() - t0, 1),
            "context_chars": len(ctx),
        }
        logger.info(f"  {label}: {results[label]['seconds']}s")

    slug = f"{repo_id.replace('/', '__')}__{module_path.replace('/', '_') or 'root'}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    doc = [
        f"# {repo_id} / {module_path}",
        "",
        f"Model: `{LLM_CONFIG.model}` (held constant)  ",
        f"Inputs: {len(file_indices)} files + {len(children)} submodules "
        f"= {n_inputs} children  ",
        f"Old context: {len(old_ctx)} chars (capped at 6000, first 15 inputs)  ",
        f"New context: {len(new_ctx)} chars",
        "",
        "## Baseline (stored in Couchbase today)", "",
        baseline or "_none stored_", "",
        "## Old context → summary", "",
        f"_{results['old']['seconds']}s_", "",
        results["old"]["summary"], "",
        "## New context → summary", "",
        f"_{results['new']['seconds']}s_", "",
        results["new"]["summary"], "",
        "## New context (verbatim prompt input)", "",
        "```", new_ctx, "```", "",
        "## Old context (verbatim prompt input)", "",
        "```", old_ctx, "```",
    ]
    (OUT_DIR / f"{slug}.md").write_text("\n".join(doc))
    logger.info(f"  wrote {OUT_DIR / f'{slug}.md'}")

    return {
        "repo_id": repo_id,
        "module_path": module_path,
        "files": len(file_indices),
        "submodules": len(children),
        **{f"{k}_{f}": v[f] for k, v in results.items() for f in ("seconds", "context_chars")},
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", help="repo_id; omit to use built-in targets")
    ap.add_argument("--module", help="module_path; required with --repo")
    args = ap.parse_args()

    if args.repo and args.module is None:
        ap.error("--module is required when --repo is given")
    targets = [(args.repo, args.module)] if args.repo else DEFAULT_TARGETS

    cb = CouchbaseClient()  # connects in __init__
    enricher = V4LLMEnricher()

    logger.info(f"Model: {LLM_CONFIG.model} @ {LLM_CONFIG.base_url} "
                f"(reasoning_effort={LLM_CONFIG.reasoning_effort})")

    rows = []
    for repo_id, module_path in targets:
        file_indices, children, baseline = gather(cb, repo_id, module_path)
        if not file_indices and not children:
            logger.warning(f"No children found for {repo_id}:{module_path} — skipping")
            continue
        rows.append(await run_one(
            enricher, repo_id, module_path, file_indices, children, baseline
        ))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "timing.json").write_text(json.dumps(rows, indent=2))
    logger.info(f"Done — {len(rows)} target(s)")


if __name__ == "__main__":
    asyncio.run(main())
