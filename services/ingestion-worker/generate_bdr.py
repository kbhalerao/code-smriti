#!/usr/bin/env python3
"""
BDR (Business Development Representative) Brief Generator

Generates business-focused abstractions of repositories for prospect matching.
Runs weekly, independent of the main ingestion pipeline.

Change detection:
- Computes hash of (repo_summary + readme content)
- Only regenerates if hash differs from stored value

Usage:
    python generate_bdr.py                    # All repos
    python generate_bdr.py --repo owner/name  # Single repo
    python generate_bdr.py --force            # Regenerate all (ignore hash)
    python generate_bdr.py --dry-run          # Preview without writing
"""

import argparse
import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List

from loguru import logger

# Snooze period for unchanged repos
SNOOZE_DAYS = 7

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import WorkerConfig
from storage.couchbase_client import CouchbaseClient
from llm_enricher import LLMEnricher, LLMConfig
from embeddings.local_generator import LocalEmbeddingGenerator
from v4.schemas import (
    RepoBDR, VersionInfo, SCHEMA_VERSION,
    make_bdr_id, make_bdr_input_hash
)


# Nemotron config for BDR generation (thinking model)
NEMOTRON_CONFIG = LLMConfig(
    provider="lmstudio",
    model="nvidia/nemotron-3-nano",
    base_url="http://macstudio.local:1234",
    temperature=0.3,
    max_tokens=8000,
    timeout_seconds=300.0,
)

# BDR prompt template
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
Terms that should trigger retrieval of this capability:
- Business terms (what prospects say)
- Technical terms (what engineers say)
- Acronyms and expansions
- Adjacent concepts

### NOT A FIT
When should the BDR disqualify? What problems does this NOT solve?

### ADJACENT OPPORTUNITIES
If a prospect needs this, what else might they need?

### COMPETITIVE CONTEXT
What alternatives exist? How is this different?
(If unknown, say "requires market research")
"""


class BDRGenerator:
    """Generates and manages BDR documents for repositories."""

    def __init__(
        self,
        dry_run: bool = False,
        force: bool = False,
        llm_config: LLMConfig = NEMOTRON_CONFIG
    ):
        self.dry_run = dry_run
        self.force = force
        self.config = WorkerConfig()

        # Initialize clients
        self.cb_client = CouchbaseClient()
        self.llm = LLMEnricher(llm_config)
        self.model_name = llm_config.model

        # Embedding generator for BDR content
        self.embedder = LocalEmbeddingGenerator()

    async def close(self):
        """Cleanup resources."""
        await self.llm.close()

    def get_all_repos(self) -> List[str]:
        """Get list of all indexed repos from Couchbase."""
        query = """
            SELECT DISTINCT repo_id
            FROM `code_kosha`
            WHERE type = 'repo_summary'
        """
        result = self.cb_client.cluster.query(query)
        return [row['repo_id'] for row in result]

    def get_repo_summary(self, repo_id: str) -> Optional[Dict]:
        """Get repo_summary document for a repo."""
        query = """
            SELECT content, commit_hash, META().id as doc_id
            FROM `code_kosha`
            WHERE type = 'repo_summary'
              AND repo_id = $repo_id
            LIMIT 1
        """
        result = self.cb_client.cluster.query(query, repo_id=repo_id)
        rows = list(result)
        return rows[0] if rows else None

    def get_module_summaries(self, repo_id: str) -> str:
        """Get concatenated module summaries for a repo."""
        query = """
            SELECT module_path, content
            FROM `code_kosha`
            WHERE type = 'module_summary'
              AND repo_id = $repo_id
              AND content IS NOT NULL
            ORDER BY module_path
            LIMIT 20
        """
        result = self.cb_client.cluster.query(query, repo_id=repo_id)
        parts = []
        for row in result:
            parts.append(f"### {row['module_path']}/\n{row['content']}")
        return "\n\n".join(parts)

    def get_readme_content(self, repo_id: str) -> str:
        """Get README content from disk."""
        # Parse owner/name from repo_id
        parts = repo_id.split('/')
        if len(parts) != 2:
            return ""

        owner, name = parts
        repo_path = Path(self.config.repos_path) / owner / name

        # Try common README filenames
        for readme_name in ['README.md', 'readme.md', 'README.rst', 'README.txt', 'README']:
            readme_path = repo_path / readme_name
            if readme_path.exists():
                try:
                    content = readme_path.read_text(encoding='utf-8', errors='ignore')
                    # Limit to first 5000 chars
                    return content[:5000]
                except Exception as e:
                    logger.warning(f"Failed to read {readme_path}: {e}")

        return ""

    def get_existing_bdr(self, repo_id: str) -> Optional[Dict]:
        """Get existing BDR document if it exists."""
        doc_id = make_bdr_id(repo_id)
        try:
            result = self.cb_client.collection.get(doc_id)
            return result.content_as[dict]
        except Exception:
            return None

    async def generate_bdr(
        self,
        repo_id: str,
        repo_summary: str,
        readme_content: str,
        module_summaries: str
    ) -> Dict:
        """Generate BDR brief using LLM with reasoning trace."""
        prompt = BDR_PROMPT.format(
            repo_id=repo_id,
            repo_summary=repo_summary,
            readme_content=readme_content or "(No README available)",
            module_summaries=module_summaries or "(No module summaries available)"
        )

        result = await self.llm.generate_with_reasoning(prompt)

        return {
            "content": result.get("output", ""),
            "reasoning": result.get("reasoning"),
            "usage": result.get("usage", {})
        }

    def generate_embedding(self, content: str) -> List[float]:
        """Generate embedding for BDR content (uses search_document prefix)."""
        return self.embedder.generate_embedding(content)

    def _is_snoozed(self, existing: Dict) -> bool:
        """Check if repo is in snooze period (checked recently, no changes)."""
        last_checked = existing.get('last_checked', '')
        if not last_checked:
            return False

        try:
            checked_at = datetime.fromisoformat(last_checked)
            snooze_until = checked_at + timedelta(days=SNOOZE_DAYS)
            return datetime.now() < snooze_until
        except (ValueError, TypeError):
            return False

    def _update_last_checked(self, repo_id: str, existing: Dict):
        """Update last_checked timestamp without regenerating content."""
        existing['last_checked'] = datetime.now().isoformat()
        doc_id = make_bdr_id(repo_id)
        self.cb_client.collection.upsert(doc_id, existing)

    async def process_repo(self, repo_id: str) -> str:
        """
        Process a single repo: check if BDR needs update, generate if needed.

        Returns:
            'updated' if BDR was generated/updated
            'skipped' if no changes needed
            'snoozed' if in snooze period
        """
        logger.info(f"Processing {repo_id}")

        # Get repo summary
        summary_doc = self.get_repo_summary(repo_id)
        if not summary_doc:
            logger.warning(f"No repo_summary found for {repo_id}, skipping")
            return 'skipped'

        repo_summary = summary_doc.get('content', '')
        commit_hash = summary_doc.get('commit_hash', '')

        # Get README and module summaries
        readme_content = self.get_readme_content(repo_id)
        module_summaries = self.get_module_summaries(repo_id)

        # Compute input hash
        input_hash = make_bdr_input_hash(repo_summary, readme_content)

        # Check existing BDR
        existing = self.get_existing_bdr(repo_id)
        if existing and not self.force:
            stored_hash = existing.get('input_hash', '')
            if stored_hash == input_hash:
                # Hash matches - check if in snooze period
                if self._is_snoozed(existing):
                    logger.info(f"  ↳ Snoozed (checked recently, no changes)")
                    return 'snoozed'
                else:
                    # Update last_checked and snooze for next week
                    if not self.dry_run:
                        self._update_last_checked(repo_id, existing)
                    logger.info(f"  ↳ BDR up-to-date, snoozing for {SNOOZE_DAYS} days")
                    return 'skipped'

        # Generate new BDR
        logger.info(f"  ↳ Generating BDR (hash: {input_hash[:8]})")

        result = await self.generate_bdr(
            repo_id=repo_id,
            repo_summary=repo_summary,
            readme_content=readme_content,
            module_summaries=module_summaries
        )

        usage = result.get("usage", {})
        reasoning_tokens = usage.get("output_tokens_details", {}).get("reasoning_tokens", 0)

        # Generate embedding for BDR content
        embedding = self.generate_embedding(result["content"])

        # Create BDR document
        bdr_doc = RepoBDR(
            document_id=make_bdr_id(repo_id),
            repo_id=repo_id,
            content=result["content"],
            reasoning_trace=result.get("reasoning"),
            input_hash=input_hash,
            source_commit=commit_hash,
            last_checked=datetime.now().isoformat(),
            embedding=embedding,
            model=self.model_name,
            generation_tokens=usage.get("output_tokens", 0),
            reasoning_tokens=reasoning_tokens,
            version=VersionInfo(
                schema_version=SCHEMA_VERSION,
                pipeline_version="bdr-1.0",
                created_at=datetime.now().isoformat(),
            )
        )

        if self.dry_run:
            logger.info(f"  ↳ [DRY RUN] Would save BDR ({len(result['content'])} chars)")
            print(f"\n{'='*60}")
            print(f"BDR BRIEF: {repo_id}")
            print(f"{'='*60}")
            if result.get("reasoning"):
                print(f"\n## REASONING\n{result['reasoning']}\n")
            print(result["content"][:2000])
            if len(result["content"]) > 2000:
                print(f"\n... ({len(result['content']) - 2000} more chars)")
            print(f"{'='*60}\n")
        else:
            # Upsert to Couchbase
            self.cb_client.collection.upsert(bdr_doc.document_id, bdr_doc.to_dict())
            logger.info(f"  ↳ Saved BDR ({len(result['content'])} chars, {usage.get('output_tokens', 0)} tokens)")

        return 'updated'

    async def run(self, repo_filter: Optional[str] = None) -> Dict:
        """
        Run BDR generation for all repos or a specific repo.

        Returns:
            Dict with stats: processed, updated, skipped, snoozed, errors
        """
        stats = {"processed": 0, "updated": 0, "skipped": 0, "snoozed": 0, "errors": 0}

        if repo_filter:
            repos = [repo_filter]
        else:
            repos = self.get_all_repos()

        logger.info(f"Processing {len(repos)} repositories")

        for repo_id in repos:
            try:
                result = await self.process_repo(repo_id)
                stats["processed"] += 1
                stats[result] += 1
            except Exception as e:
                logger.error(f"Error processing {repo_id}: {e}")
                stats["processed"] += 1
                stats["errors"] += 1

        return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Generate BDR briefs for repositories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        "--repo",
        type=str,
        help="Single repo to process (format: owner/name)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without writing to database"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Regenerate all BDRs (ignore hash check)"
    )

    args = parser.parse_args()

    generator = BDRGenerator(
        dry_run=args.dry_run,
        force=args.force
    )

    try:
        stats = await generator.run(repo_filter=args.repo)

        logger.info(f"\nBDR Generation Complete:")
        logger.info(f"  Processed: {stats['processed']}")
        logger.info(f"  Updated:   {stats['updated']}")
        logger.info(f"  Skipped:   {stats['skipped']}")
        logger.info(f"  Snoozed:   {stats['snoozed']}")
        logger.info(f"  Errors:    {stats['errors']}")

        if stats['errors'] > 0:
            sys.exit(1)

    finally:
        await generator.close()


if __name__ == "__main__":
    asyncio.run(main())
