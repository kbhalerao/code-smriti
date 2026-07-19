"""
Backfill LLM summaries for module_summary docs still at enrichment_level=basic.

Run AFTER scripts/purge_orphan_summaries.py: the purge leaves only
current-commit module docs, so every remaining `basic` module_summary is live
and worth upgrading. For each we gather its children's summaries (the same
context the aggregator's LLM path uses), regenerate the summary with the LLM,
re-embed, and upsert in place.

Model: general (alias -> qwen3.5:35b-mlx, pinned/always-loaded) via ollama's
/v1/responses with reasoning disabled — matches RAG serving, avoids cold-load.

Usage:
    ./.venv/bin/python scripts/backfill_module_summaries.py --limit 5   # sample
    ./.venv/bin/python scripts/backfill_module_summaries.py --dry-run   # no writes
    ./.venv/bin/python scripts/backfill_module_summaries.py             # full run
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).resolve().parent.parent.parent.parent / ".env")

from storage.couchbase_client import CouchbaseClient
from embeddings.local_generator import LocalEmbeddingGenerator
from llm_enricher import LLMConfig
from v4.llm_enricher import V4LLMEnricher
from v4.schemas import EnrichmentLevel

BUCKET = "code_kosha"
BACKFILL_MODEL = "general"
MAX_CONTEXT_SUMMARIES = 15  # matches aggregator._llm_module_summary


def basic_modules(cb: CouchbaseClient, limit: int | None):
    """Current-commit module_summary docs still at enrichment_level=basic."""
    query = f"""
        SELECT META().id AS id, repo_id, module_path, children_ids
        FROM `{BUCKET}`
        WHERE type = 'module_summary'
          AND quality.enrichment_level = 'basic'
        ORDER BY repo_id, module_path
    """
    if limit:
        query += f"\n        LIMIT {limit}"
    return list(cb.cluster.query(query))


def child_summaries(cb: CouchbaseClient, children_ids: list) -> list[str]:
    """Content of a module's children (file_index / module_summary docs)."""
    contents: list[str] = []
    for child_id in children_ids or []:
        try:
            doc = cb.collection.get(child_id).content_as[dict]
        except Exception:
            continue  # child from a superseded commit; skip
        if doc.get("type") in ("file_index", "module_summary"):
            text = doc.get("content")
            if text:
                contents.append(text)
    return contents


async def run(dry_run: bool, limit: int | None) -> int:
    cb = CouchbaseClient()

    config = LLMConfig(
        provider=os.getenv("LLM_PROVIDER", "ollama"),
        model=BACKFILL_MODEL,
        base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434"),
        temperature=0.3,
        max_tokens=2000,
        timeout_seconds=120.0,
        reasoning_effort="none",
    )
    enricher = V4LLMEnricher(config)
    embed_gen = LocalEmbeddingGenerator()

    modules = basic_modules(cb, limit)
    logger.info(f"Found {len(modules)} basic module summaries to backfill "
                f"(model={BACKFILL_MODEL}, dry_run={dry_run})")

    upgraded = skipped_no_context = failed = 0
    for i, mod in enumerate(modules, 1):
        doc_id = mod["id"]
        repo_id = mod.get("repo_id")
        module_path = mod.get("module_path") or "(root)"

        contents = child_summaries(cb, mod.get("children_ids"))
        if not contents:
            skipped_no_context += 1
            continue

        context = "\n\n---\n\n".join(contents[:MAX_CONTEXT_SUMMARIES])
        try:
            result = await enricher.enrich_module(
                module_path=module_path, files_context=context, repo_id=repo_id
            )
            summary = (result.get("summary") or "").strip()
        except Exception as e:
            logger.warning(f"  LLM failed for {repo_id}/{module_path}: {e}")
            failed += 1
            continue

        if not summary:
            failed += 1
            continue

        if dry_run:
            logger.info(f"[{i}/{len(modules)}] {repo_id}/{module_path}\n    {summary[:200]}")
            upgraded += 1
            continue

        try:
            doc = cb.collection.get(doc_id).content_as[dict]
            doc["content"] = summary
            doc.setdefault("quality", {})["enrichment_level"] = EnrichmentLevel.LLM_SUMMARY.value
            doc["embedding"] = embed_gen.generate_embedding(summary)
            cb.collection.upsert(doc_id, doc)
            upgraded += 1
        except Exception as e:
            logger.warning(f"  Upsert failed for {doc_id}: {e}")
            failed += 1
            continue

        if i % 50 == 0:
            logger.info(f"  progress {i}/{len(modules)} "
                        f"(upgraded={upgraded}, no_context={skipped_no_context}, failed={failed})")

    logger.info(f"\nDone. upgraded={upgraded}, "
                f"skipped_no_context={skipped_no_context}, failed={failed}")
    await enricher.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill basic module summaries with LLM")
    parser.add_argument("--dry-run", action="store_true", help="Generate but do not write")
    parser.add_argument("--limit", type=int, default=None, help="Only process N modules")
    args = parser.parse_args()
    return asyncio.run(run(args.dry_run, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
