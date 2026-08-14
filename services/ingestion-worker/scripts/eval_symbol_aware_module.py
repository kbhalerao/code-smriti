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
from v4.schemas import FileIndex, ModuleSummary, SymbolRef
from v4.doc_versions import file_key, newest_per_key
from v4.module_context import build_module_context
from v4.llm_enricher import V4LLMEnricher
from llm_enricher import LLM_CONFIG

DEFAULT_TARGETS: List[Tuple[str, str]] = [
    ("kbhalerao/agkit.io-backend", "tier1apps/gislayers"),
]

OUT_DIR = Path(__file__).parent / "eval_symbol_aware_results"


def _is_direct_child(file_path: str, module_path: str) -> bool:
    """True when file_path is directly inside module_path (no nested subdir)."""
    if not module_path:
        return "/" not in file_path
    if not file_path.startswith(f"{module_path}/"):
        return False
    return "/" not in file_path[len(module_path) + 1:]


def _is_direct_child_module(child: str, module_path: str) -> bool:
    if not module_path:
        return "/" not in child and child != ""
    if not child.startswith(f"{module_path}/"):
        return False
    return "/" not in child[len(module_path) + 1:]


def _to_file_index(row: Dict) -> FileIndex:
    """Rebuild a FileIndex from its stored Couchbase document."""
    meta = row.get("metadata") or {}
    symbols = []
    for s in meta.get("symbols") or []:
        lines = s.get("lines") or [0, 0]
        symbols.append(SymbolRef(
            name=s.get("name", ""),
            symbol_type=s.get("type", ""),
            start_line=lines[0] if len(lines) > 0 else 0,
            end_line=lines[1] if len(lines) > 1 else 0,
            docstring=s.get("docstring"),
            methods=s.get("methods") or [],
        ))
    return FileIndex(
        document_id=row.get("document_id", ""),
        repo_id=row.get("repo_id", ""),
        file_path=row.get("file_path", ""),
        commit_hash=row.get("commit_hash", ""),
        content=row.get("content") or "",
        line_count=meta.get("line_count", 0),
        language=meta.get("language", "unknown"),
        imports=meta.get("imports") or [],
        symbols=symbols,
    )


def _to_module_summary(row: Dict) -> ModuleSummary:
    meta = row.get("metadata") or {}
    return ModuleSummary(
        document_id=row.get("document_id", ""),
        repo_id=row.get("repo_id", ""),
        module_path=row.get("module_path", ""),
        commit_hash=row.get("commit_hash", ""),
        content=row.get("content") or "",
        file_count=meta.get("file_count", 0),
    )


def _module_key(doc: Dict) -> tuple:
    return (doc.get("repo_id") or "", doc.get("module_path") or "")


def gather(cb: CouchbaseClient, repo_id: str, module_path: str):
    """Pull the file and child-module docs the aggregator would see."""
    file_rows = list(cb.cluster.query(
        'SELECT document_id, repo_id, file_path, commit_hash, content, metadata, version '
        'FROM `code_kosha` '
        'WHERE type="file_index" AND repo_id=$repo_id AND content IS NOT NULL '
        'ORDER BY file_path',
        repo_id=repo_id,
    ))
    file_indices = [
        _to_file_index(r) for r in newest_per_key(file_rows, file_key)
        if _is_direct_child(r.get("file_path", ""), module_path)
    ]

    mod_rows = list(cb.cluster.query(
        'SELECT document_id, repo_id, module_path, commit_hash, content, metadata, version '
        'FROM `code_kosha` '
        'WHERE type="module_summary" AND repo_id=$repo_id AND content IS NOT NULL',
        repo_id=repo_id,
    ))
    latest_mods = newest_per_key(mod_rows, _module_key)
    children = [
        _to_module_summary(r) for r in latest_mods
        if _is_direct_child_module(r.get("module_path", ""), module_path)
    ]
    baseline = next(
        (r.get("content") for r in latest_mods if r.get("module_path") == module_path),
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
