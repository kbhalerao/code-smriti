#!/usr/bin/env python3
"""
Regenerate module_summary docs using symbol-aware context.

Existing module summaries were built from a concatenation of their children's
prose summaries, which discarded every symbol name, docstring and import the file
pass had extracted. This rebuilds each one from `v4.module_context`, which reads
`file_index.metadata` directly.

Ordering matters. A module's context includes its submodules' summaries, so
regenerating a parent before its children would fold stale text into fresh
output. Modules are therefore processed deepest-first: all depth-N modules
complete before any depth-(N-1) module starts, which preserves the bottom-up
property the aggregator relies on while still allowing concurrency within a
depth.

Children are resolved by path, not by the stored `children_ids`. Those ids can
reference documents the superseded-doc purge removed, and they encode the
duplicate-inflated child set that motivated this work in the first place.

Only modules that still need upgrading are regenerated. That makes the script
resumable by construction: if the model goes away mid-run — as it did on
2026-08-14, when the LLM stopped accepting connections and the circuit breaker
failed the remaining ~2,990 modules in eleven seconds — rerunning it picks up
exactly what is left, rather than spending hours redoing finished work. Pass
`--force-all` to regenerate everything regardless.

Usage:
    ./.venv/bin/python scripts/backfill_symbol_aware_summaries.py --dry-run --limit 5
    ./.venv/bin/python scripts/backfill_symbol_aware_summaries.py --repo kbhalerao/agkit.io-backend
    ./.venv/bin/python scripts/backfill_symbol_aware_summaries.py --concurrency 6
"""

import argparse
import asyncio
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from couchbase.options import QueryOptions
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

from embeddings.local_generator import LocalEmbeddingGenerator
from llm_enricher import LLM_CONFIG
from storage.couchbase_client import CouchbaseClient
from v4.doc_loader import (
    load_repo_file_docs,
    load_repo_module_docs,
    resolve_module_children,
)
from v4.llm_enricher import V4LLMEnricher
from v4.module_context import build_module_context
from v4.schemas import EnrichmentLevel

BUCKET = "code_kosha"


def module_depth(module_path: str) -> int:
    """Root is depth 0; each path segment adds one."""
    return len(module_path.split("/")) if module_path else 0


# A module counts as upgraded only when it holds an LLM summary that was actually
# built from symbols. Anything else — the old prose path, a structural fallback,
# a summary carried forward from before this migration — is still work to do.
# Expressed twice, once in N1QL to skip whole repos without loading them and once
# in Python to select modules within a repo; the two must agree.
UPGRADED_SOURCE = "aggregated_from_symbols"

STALE_PREDICATE_N1QL = (
    f"(IFMISSINGORNULL(quality.summary_source, '') != '{UPGRADED_SOURCE}'"
    f" OR IFMISSINGORNULL(quality.enrichment_level, '') != "
    f"'{EnrichmentLevel.LLM_SUMMARY.value}')"
)


def is_stale(mod_doc: Dict) -> bool:
    quality = mod_doc.get("quality") or {}
    return not (
        quality.get("summary_source") == UPGRADED_SOURCE
        and quality.get("enrichment_level") == EnrichmentLevel.LLM_SUMMARY.value
    )


def repos_with_modules(
    cb: CouchbaseClient, repo_filter: str, stale_only: bool
) -> List[str]:
    where = "type = 'module_summary'"
    if stale_only:
        where += f" AND {STALE_PREDICATE_N1QL}"
    params = {}
    if repo_filter:
        where += " AND repo_id = $repo_id"
        params["repo_id"] = repo_filter
    query = f"SELECT DISTINCT repo_id FROM `{BUCKET}` WHERE {where}"
    rows = cb.cluster.query(query, QueryOptions(named_parameters=params)) if params \
        else cb.cluster.query(query)
    return sorted(r["repo_id"] for r in rows if r.get("repo_id"))


async def regenerate_one(
    enricher: V4LLMEnricher,
    embed_gen: LocalEmbeddingGenerator,
    cb: CouchbaseClient,
    mod_doc: Dict,
    file_docs: List[Dict],
    module_docs: List[Dict],
    dry_run: bool,
) -> str:
    """Returns one of: 'upgraded', 'no_context', 'failed'."""
    repo_id = mod_doc.get("repo_id") or ""
    module_path = mod_doc.get("module_path") or ""
    doc_id = mod_doc.get("document_id") or ""

    files, submodules = resolve_module_children(file_docs, module_docs, module_path)
    context = build_module_context(files, submodules)
    if not context.strip():
        return "no_context"

    try:
        result = await enricher.enrich_module_symbol_aware(
            module_path=module_path or "(root)",
            module_context=context,
            repo_id=repo_id,
        )
        summary = (result.get("summary") or "").strip()
    except Exception as e:
        logger.warning(f"  LLM failed for {repo_id}/{module_path}: {e}")
        return "failed"

    if not summary:
        return "failed"

    if dry_run:
        logger.info(f"  [dry-run] {repo_id}/{module_path or '(root)'}\n      {summary[:220]}")
        return "upgraded"

    try:
        doc = cb.collection.get(doc_id).content_as[dict]
        doc["content"] = summary
        doc["embedding"] = embed_gen.generate_embedding(summary)
        quality = doc.setdefault("quality", {})
        quality["enrichment_level"] = EnrichmentLevel.LLM_SUMMARY.value
        quality["summary_source"] = "aggregated_from_symbols"
        # file_count was inflated by superseded duplicates; restate it from the
        # resolved child set rather than leaving the old number in place.
        doc.setdefault("metadata", {})["file_count"] = len(files)
        cb.collection.upsert(doc_id, doc)
        return "upgraded"
    except Exception as e:
        logger.warning(f"  Upsert failed for {doc_id}: {e}")
        return "failed"


async def process_repo(
    enricher: V4LLMEnricher,
    embed_gen: LocalEmbeddingGenerator,
    cb: CouchbaseClient,
    repo_id: str,
    concurrency: int,
    dry_run: bool,
    limit: int,
    stale_only: bool,
) -> Dict[str, int]:
    module_docs = load_repo_module_docs(cb.cluster, repo_id)
    if not module_docs:
        return {}

    # Narrow to the modules needing work before loading files. Children are still
    # resolved against the *full* module set, so an upgraded submodule's summary
    # still reaches its stale parent's context.
    module_docs_to_do = [d for d in module_docs if is_stale(d)] if stale_only \
        else module_docs
    if not module_docs_to_do:
        return {}

    file_docs = load_repo_file_docs(cb.cluster, repo_id)

    if limit:
        module_docs_to_do = module_docs_to_do[:limit]

    by_depth: Dict[int, List[Dict]] = defaultdict(list)
    for d in module_docs_to_do:
        by_depth[module_depth(d.get("module_path") or "")].append(d)

    tally: Dict[str, int] = defaultdict(int)
    sem = asyncio.Semaphore(concurrency)

    async def run(mod_doc, sibling_docs):
        async with sem:
            outcome = await regenerate_one(
                enricher, embed_gen, cb, mod_doc, file_docs, sibling_docs, dry_run
            )
            tally[outcome] += 1

    # Deepest first: every child depth finishes before its parents begin, and the
    # module docs are re-read between depths so a parent's context picks up the
    # summaries its children just wrote. A dry run writes nothing, so re-reading
    # would only cost queries.
    for depth in sorted(by_depth, reverse=True):
        current = module_docs if dry_run else load_repo_module_docs(cb.cluster, repo_id)
        await asyncio.gather(*(run(m, current) for m in by_depth[depth]))

    return dict(tally)


async def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill symbol-aware module summaries")
    ap.add_argument("--repo", default="", help="Restrict to a single repo_id")
    ap.add_argument("--limit", type=int, default=0, help="Max modules per repo (sampling)")
    ap.add_argument("--concurrency", type=int, default=4, help="Parallel LLM calls")
    ap.add_argument("--dry-run", action="store_true", help="Generate but do not write")
    ap.add_argument(
        "--force-all", action="store_true",
        help="Regenerate every module, including ones already symbol-aware",
    )
    args = ap.parse_args()
    stale_only = not args.force_all

    cb = CouchbaseClient()
    enricher = V4LLMEnricher()
    embed_gen = LocalEmbeddingGenerator()

    repos = repos_with_modules(cb, args.repo, stale_only)
    logger.info(
        f"Model: {LLM_CONFIG.model} @ {LLM_CONFIG.base_url} | repos: {len(repos)} | "
        f"concurrency: {args.concurrency} | dry_run: {args.dry_run} | "
        f"scope: {'stale only' if stale_only else 'ALL modules'}"
    )

    totals: Dict[str, int] = defaultdict(int)
    started = time.time()
    for i, repo_id in enumerate(repos, 1):
        t0 = time.time()
        tally = await process_repo(
            enricher, embed_gen, cb, repo_id, args.concurrency, args.dry_run,
            args.limit, stale_only,
        )
        if not tally:
            continue
        for k, v in tally.items():
            totals[k] += v
        done = sum(totals.values())
        logger.info(
            f"[{i}/{len(repos)}] {repo_id}: {dict(tally)} "
            f"({time.time() - t0:.0f}s, {done:,} modules done, "
            f"{(time.time() - started) / 60:.0f}m elapsed)"
        )

    logger.info(f"\n{'=' * 60}")
    logger.info(f"TOTAL {dict(totals)} in {(time.time() - started) / 60:.1f} minutes")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
