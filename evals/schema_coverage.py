#!/usr/bin/env python3
"""
V4 Schema Coverage Evaluator

Verifies that V4 ingestion created all expected documents:
- Every repo has a repo_summary
- Every folder has a module_summary
- Every code file has a file_index
- Significant symbols (>=5 lines) have symbol_index
"""
import asyncio
import json
from datetime import datetime
from typing import Dict, List, Any
from pathlib import Path
import os
import sys

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions
from couchbase.auth import PasswordAuthenticator
from dotenv import load_dotenv

load_dotenv()


class SchemaCoverageEvaluator:
    """Evaluate V4 schema coverage in Couchbase."""

    def __init__(self):
        self.cluster = None
        self.bucket = None
        self.collection = None

    async def connect(self):
        """Connect to Couchbase."""
        host = os.getenv("COUCHBASE_HOST", "localhost")
        bucket_name = os.getenv("COUCHBASE_BUCKET", "code_kosha")
        username = os.getenv("COUCHBASE_USERNAME", "Administrator")
        password = os.getenv("COUCHBASE_PASSWORD", "")

        auth = PasswordAuthenticator(username, password)
        self.cluster = Cluster(f"couchbase://{host}", ClusterOptions(auth))
        self.bucket = self.cluster.bucket(bucket_name)
        self.collection = self.bucket.default_collection()
        print(f"Connected to Couchbase: {host}/{bucket_name}")

    def query(self, n1ql: str) -> List[Dict]:
        """Execute N1QL query and return results."""
        result = self.cluster.query(n1ql)
        return list(result.rows())

    def evaluate_repo_coverage(self) -> Dict[str, Any]:
        """Check that all repos have repo_summary documents."""
        print("\n[1/4] Evaluating repo_summary coverage...")

        # Get all unique repo_ids
        all_repos = self.query("""
            SELECT DISTINCT repo_id
            FROM `code_kosha`
            WHERE version.schema_version = 'v4.0'
        """)
        all_repo_ids = {r['repo_id'] for r in all_repos}

        # Get repos with repo_summary
        repos_with_summary = self.query("""
            SELECT repo_id
            FROM `code_kosha`
            WHERE type = 'repo_summary'
              AND version.schema_version = 'v4.0'
        """)
        repos_with_summary_ids = {r['repo_id'] for r in repos_with_summary}

        missing = all_repo_ids - repos_with_summary_ids

        result = {
            "total_repos": len(all_repo_ids),
            "repos_with_summary": len(repos_with_summary_ids),
            "missing_summaries": list(missing),
            "coverage_pct": len(repos_with_summary_ids) / len(all_repo_ids) * 100 if all_repo_ids else 0,
            "pass": len(missing) == 0
        }

        status = "PASS" if result['pass'] else "FAIL"
        print(f"  {status}: {result['repos_with_summary']}/{result['total_repos']} repos have repo_summary")
        if missing:
            print(f"  Missing: {', '.join(list(missing)[:5])}{'...' if len(missing) > 5 else ''}")

        return result

    def evaluate_module_coverage(self) -> Dict[str, Any]:
        """Check module_summary coverage per repo."""
        print("\n[2/4] Evaluating module_summary coverage...")

        # Get count of modules per repo
        module_counts = self.query("""
            SELECT repo_id, COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'module_summary'
              AND version.schema_version = 'v4.0'
            GROUP BY repo_id
            ORDER BY count DESC
        """)

        # Get total count
        total = self.query("""
            SELECT COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'module_summary'
              AND version.schema_version = 'v4.0'
        """)[0]['count']

        # Sample check: look for repos with very few modules (suspicious)
        repos_with_few_modules = [r for r in module_counts if r['count'] < 3]

        result = {
            "total_modules": total,
            "repos_with_modules": len(module_counts),
            "avg_modules_per_repo": total / len(module_counts) if module_counts else 0,
            "repos_with_few_modules": [r['repo_id'] for r in repos_with_few_modules],
            "top_repos": module_counts[:5] if module_counts else [],
            "pass": total > 0 and len(repos_with_few_modules) < len(module_counts) * 0.1
        }

        status = "PASS" if result['pass'] else "WARN"
        print(f"  {status}: {total} module_summary docs across {len(module_counts)} repos")
        print(f"  Avg modules/repo: {result['avg_modules_per_repo']:.1f}")

        return result

    def evaluate_file_coverage(self) -> Dict[str, Any]:
        """Check file_index coverage."""
        print("\n[3/4] Evaluating file_index coverage...")

        # Get file counts per repo
        file_counts = self.query("""
            SELECT repo_id, COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'file_index'
              AND version.schema_version = 'v4.0'
            GROUP BY repo_id
            ORDER BY count DESC
        """)

        total = self.query("""
            SELECT COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'file_index'
              AND version.schema_version = 'v4.0'
        """)[0]['count']

        # Check for files missing embeddings
        missing_embeddings = self.query("""
            SELECT COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'file_index'
              AND version.schema_version = 'v4.0'
              AND embedding IS MISSING
        """)[0]['count']

        # Check for files missing content
        missing_content = self.query("""
            SELECT COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'file_index'
              AND version.schema_version = 'v4.0'
              AND (content IS MISSING OR content = '')
        """)[0]['count']

        result = {
            "total_files": total,
            "repos_with_files": len(file_counts),
            "avg_files_per_repo": total / len(file_counts) if file_counts else 0,
            "missing_embeddings": missing_embeddings,
            "missing_content": missing_content,
            "top_repos": file_counts[:5] if file_counts else [],
            "pass": missing_embeddings == 0 and missing_content < total * 0.05
        }

        status = "PASS" if result['pass'] else "WARN"
        print(f"  {status}: {total} file_index docs across {len(file_counts)} repos")
        print(f"  Missing embeddings: {missing_embeddings}, Missing content: {missing_content}")

        return result

    def evaluate_symbol_coverage(self) -> Dict[str, Any]:
        """Check symbol_index coverage."""
        print("\n[4/4] Evaluating symbol_index coverage...")

        # Get symbol counts per repo
        symbol_counts = self.query("""
            SELECT repo_id, COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'symbol_index'
              AND version.schema_version = 'v4.0'
            GROUP BY repo_id
            ORDER BY count DESC
        """)

        total = self.query("""
            SELECT COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'symbol_index'
              AND version.schema_version = 'v4.0'
        """)[0]['count']

        # Check symbol name quality (not "unknown")
        unknown_symbols = self.query("""
            SELECT COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'symbol_index'
              AND version.schema_version = 'v4.0'
              AND (symbol_name IS MISSING OR symbol_name = 'unknown' OR symbol_name = '')
        """)[0]['count']

        # Check symbol types
        symbol_types = self.query("""
            SELECT symbol_type, COUNT(*) as `count`
            FROM `code_kosha`
            WHERE type = 'symbol_index'
              AND version.schema_version = 'v4.0'
            GROUP BY symbol_type
        """)

        result = {
            "total_symbols": total,
            "repos_with_symbols": len(symbol_counts),
            "avg_symbols_per_repo": total / len(symbol_counts) if symbol_counts else 0,
            "unknown_symbol_names": unknown_symbols,
            "symbol_types": {r['symbol_type']: r['count'] for r in symbol_types},
            "top_repos": symbol_counts[:5] if symbol_counts else [],
            "pass": unknown_symbols == 0
        }

        status = "PASS" if result['pass'] else "FAIL"
        print(f"  {status}: {total} symbol_index docs, {unknown_symbols} with unknown names")
        print(f"  Symbol types: {result['symbol_types']}")

        return result

    def evaluate_quality_metrics(self) -> Dict[str, Any]:
        """Check quality.enrichment_level distribution."""
        print("\n[Bonus] Evaluating enrichment quality...")

        # Get enrichment level distribution
        # Note: 'level' is a reserved word in N1QL, must use backticks
        enrichment = self.query("""
            SELECT quality.enrichment_level as `level`, COUNT(*) as `count`
            FROM `code_kosha`
            WHERE version.schema_version = 'v4.0'
            GROUP BY quality.enrichment_level
        """)

        enrichment_dist = {r['level']: r['count'] for r in enrichment}
        total = sum(enrichment_dist.values())

        llm_summary_pct = enrichment_dist.get('llm_summary', 0) / total * 100 if total else 0

        result = {
            "enrichment_distribution": enrichment_dist,
            "llm_summary_pct": llm_summary_pct,
            "pass": llm_summary_pct > 80
        }

        status = "PASS" if result['pass'] else "WARN"
        print(f"  {status}: {llm_summary_pct:.1f}% documents have LLM summaries")
        print(f"  Distribution: {enrichment_dist}")

        return result

    async def run_all(self) -> Dict[str, Any]:
        """Run all coverage evaluations."""
        await self.connect()

        print("=" * 70)
        print("V4 SCHEMA COVERAGE EVALUATION")
        print("=" * 70)

        results = {
            "timestamp": datetime.now().isoformat(),
            "evaluations": {
                "repo_coverage": self.evaluate_repo_coverage(),
                "module_coverage": self.evaluate_module_coverage(),
                "file_coverage": self.evaluate_file_coverage(),
                "symbol_coverage": self.evaluate_symbol_coverage(),
                "quality_metrics": self.evaluate_quality_metrics(),
            }
        }

        # Overall pass/fail
        all_pass = all(
            results["evaluations"][k].get("pass", False)
            for k in ["repo_coverage", "symbol_coverage"]  # Critical checks
        )
        results["overall_pass"] = all_pass

        print("\n" + "=" * 70)
        print(f"OVERALL: {'PASS' if all_pass else 'FAIL'}")
        print("=" * 70)

        return results


async def main():
    evaluator = SchemaCoverageEvaluator()
    results = await evaluator.run_all()

    # Save results
    output_file = Path(__file__).parent / f"schema_coverage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")

    return results


if __name__ == "__main__":
    asyncio.run(main())
