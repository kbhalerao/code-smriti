#!/usr/bin/env python3
"""
V4 Search Quality Evaluator

Tests the /api/rag/search endpoint at all granularity levels:
- symbol: Find specific functions/classes
- file: Find relevant files
- module: Find relevant folders
- repo: High-level repository understanding

Metrics:
- Hit rate: % queries that find expected results
- Precision@K: % of top-K results that are relevant
- MRR: Mean Reciprocal Rank of first relevant result
- Score distribution: Embedding similarity scores
"""
import asyncio
import json
import httpx
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
import os
import sys

from dotenv import load_dotenv
load_dotenv()


class SearchQualityEvaluator:
    """Evaluate V4 search quality across all levels."""

    def __init__(self, api_url: str = None):
        self.api_url = api_url or os.getenv("CODESMRITI_API_URL", "http://localhost")
        self.username = os.getenv("CODESMRITI_USERNAME") or os.getenv("API_USERNAME", "")
        self.password = os.getenv("CODESMRITI_PASSWORD") or os.getenv("API_PASSWORD", "")
        self.token: Optional[str] = None

    async def get_token(self) -> str:
        """Get authentication token."""
        if self.token:
            return self.token

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{self.api_url}/api/auth/login",
                json={"email": self.username, "password": self.password},
                timeout=30.0
            )
            response.raise_for_status()
            self.token = response.json()["token"]
            return self.token

    async def search(
        self,
        query: str,
        level: str = "file",
        limit: int = 10,
        repo_filter: str = None
    ) -> Dict[str, Any]:
        """Execute a search query."""
        token = await self.get_token()

        payload = {
            "query": query,
            "level": level,
            "limit": limit
        }
        if repo_filter:
            payload["repo_filter"] = repo_filter

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{self.api_url}/api/rag/search",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=60.0
            )
            response.raise_for_status()
            return response.json()

    def evaluate_results(
        self,
        results: List[Dict],
        expected_repos: List[str],
        expected_files: List[str],
        expected_symbols: List[str] = None
    ) -> Dict[str, Any]:
        """Evaluate search results against expected values."""
        if not results:
            return {
                "hit": False,
                "precision_at_1": 0.0,
                "precision_at_3": 0.0,
                "precision_at_5": 0.0,
                "mrr": 0.0,
                "avg_score": 0.0,
                "top_score": 0.0,
                "result_count": 0
            }

        # Extract result attributes (handle None values and ensure types)
        result_repos = [r.get("repo_id") or "" for r in results]
        result_files = [r.get("file_path") or "" for r in results]
        result_symbols = [r.get("symbol_name") or "" for r in results]
        # Ensure scores are floats, not lists or other types
        scores = []
        for r in results:
            s = r.get("score")
            if isinstance(s, (int, float)):
                scores.append(float(s))
            else:
                scores.append(0.0)

        def matches_any(value: str, expected: List[str]) -> bool:
            """Check if value matches any expected pattern."""
            if not value or not expected:
                return False
            return any(exp.lower() in value.lower() for exp in expected if exp)

        # Calculate relevance for each result
        relevant = []
        for i, r in enumerate(results):
            is_relevant = (
                matches_any(result_repos[i], expected_repos) or
                matches_any(result_files[i], expected_files) or
                (bool(expected_symbols) and matches_any(result_symbols[i], expected_symbols))
            )
            relevant.append(bool(is_relevant))

        # Hit rate: did we find any relevant result?
        hit = any(relevant)

        # Precision@K
        def precision_at_k(k: int) -> float:
            if len(relevant) < k:
                return sum(relevant) / len(relevant) if relevant else 0.0
            return sum(relevant[:k]) / k

        # MRR: Reciprocal rank of first relevant result
        mrr = 0.0
        for i, is_rel in enumerate(relevant):
            if is_rel:
                mrr = 1.0 / (i + 1)
                break

        return {
            "hit": hit,
            "precision_at_1": precision_at_k(1),
            "precision_at_3": precision_at_k(3),
            "precision_at_5": precision_at_k(5),
            "mrr": mrr,
            "avg_score": sum(scores) / len(scores) if scores else 0.0,
            "top_score": max(scores) if scores else 0.0,
            "result_count": len(results),
            "relevant_count": sum(relevant)
        }

    async def run_eval_suite(
        self,
        questions: List[Dict],
        levels: List[str] = None
    ) -> Dict[str, Any]:
        """Run evaluation suite across specified levels."""
        levels = levels or ["symbol", "file", "module", "repo"]
        results_by_level = {level: [] for level in levels}

        print("=" * 70)
        print("V4 SEARCH QUALITY EVALUATION")
        print("=" * 70)
        print(f"Questions: {len(questions)}")
        print(f"Levels: {', '.join(levels)}")
        print("=" * 70)

        for q_idx, question in enumerate(questions, 1):
            query = question["query"]
            expected_repos = question.get("expected_repos", [])
            expected_files = question.get("expected_files", [])
            expected_symbols = question.get("expected_symbols", [])
            category = question.get("category", "general")

            print(f"\n[{q_idx}/{len(questions)}] Q: {query[:60]}...")

            for level in levels:
                try:
                    search_result = await self.search(query, level=level, limit=10)
                    results = search_result.get("results", [])

                    metrics = self.evaluate_results(
                        results, expected_repos, expected_files, expected_symbols
                    )
                    metrics.update({
                        "query": query,
                        "category": category,
                        "level": level,
                        "expected_repos": expected_repos,
                        "expected_files": expected_files,
                        "top_results": [
                            {
                                "repo_id": r.get("repo_id"),
                                "file_path": r.get("file_path"),
                                "symbol_name": r.get("symbol_name"),
                                "score": r.get("score")
                            }
                            for r in results[:3]
                        ]
                    })

                    results_by_level[level].append(metrics)

                    status = "HIT" if metrics["hit"] else "MISS"
                    print(f"  {level:8s}: {status} | P@3={metrics['precision_at_3']:.2f} | score={metrics['top_score']:.2f}")

                except Exception as e:
                    print(f"  {level:8s}: ERROR - {str(e)[:50]}")
                    results_by_level[level].append({
                        "query": query,
                        "level": level,
                        "error": str(e)
                    })

            # Small delay between questions
            await asyncio.sleep(0.1)

        # Aggregate metrics
        summary = {}
        for level in levels:
            level_results = [r for r in results_by_level[level] if "error" not in r]
            if level_results:
                summary[level] = {
                    "total_questions": len(level_results),
                    "hit_rate": sum(1 for r in level_results if r["hit"]) / len(level_results),
                    "avg_precision_at_1": sum(r["precision_at_1"] for r in level_results) / len(level_results),
                    "avg_precision_at_3": sum(r["precision_at_3"] for r in level_results) / len(level_results),
                    "avg_precision_at_5": sum(r["precision_at_5"] for r in level_results) / len(level_results),
                    "avg_mrr": sum(r["mrr"] for r in level_results) / len(level_results),
                    "avg_score": sum(r["avg_score"] for r in level_results) / len(level_results),
                }

        print("\n" + "=" * 70)
        print("SUMMARY BY LEVEL")
        print("=" * 70)
        for level, stats in summary.items():
            print(f"\n{level.upper()}:")
            print(f"  Hit Rate:    {stats['hit_rate']*100:.1f}%")
            print(f"  P@1:         {stats['avg_precision_at_1']:.3f}")
            print(f"  P@3:         {stats['avg_precision_at_3']:.3f}")
            print(f"  MRR:         {stats['avg_mrr']:.3f}")
            print(f"  Avg Score:   {stats['avg_score']:.3f}")

        return {
            "timestamp": datetime.now().isoformat(),
            "config": {
                "api_url": self.api_url,
                "levels": levels,
                "question_count": len(questions)
            },
            "summary": summary,
            "results_by_level": results_by_level
        }


def load_eval_questions(questions_file: str = None) -> List[Dict]:
    """Load evaluation questions from file or use defaults."""
    if questions_file and Path(questions_file).exists():
        with open(questions_file) as f:
            data = json.load(f)
            return data.get("questions", data)

    # Default V4 evaluation questions
    return [
        # Symbol-level queries
        {
            "query": "job_counter decorator",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["decorators.py"],
            "expected_symbols": ["job_counter"],
            "category": "symbol_specific"
        },
        {
            "query": "FilteredQuerySetMixin class",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["role_privileges.py"],
            "expected_symbols": ["FilteredQuerySetMixin"],
            "category": "symbol_specific"
        },
        {
            "query": "authenticate user function",
            "expected_repos": [],
            "expected_files": ["auth", "login", "backend"],
            "expected_symbols": ["authenticate"],
            "category": "symbol_pattern"
        },

        # File-level queries
        {
            "query": "Django models for user management",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["models.py", "associates"],
            "expected_symbols": [],
            "category": "file_pattern"
        },
        {
            "query": "Celery task definitions",
            "expected_repos": [],
            "expected_files": ["tasks.py", "celery"],
            "expected_symbols": [],
            "category": "file_pattern"
        },
        {
            "query": "API endpoints for samples",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["views.py", "api.py", "samples"],
            "expected_symbols": [],
            "category": "file_pattern"
        },

        # Module-level queries
        {
            "query": "authentication module",
            "expected_repos": [],
            "expected_files": ["auth", "authentication", "login"],
            "expected_symbols": [],
            "category": "module_pattern"
        },
        {
            "query": "test utilities",
            "expected_repos": [],
            "expected_files": ["test", "tests", "conftest"],
            "expected_symbols": [],
            "category": "module_pattern"
        },

        # Repo-level queries
        {
            "query": "laboratory information management system",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": [],
            "expected_symbols": [],
            "category": "repo_overview"
        },
        {
            "query": "Django multi-tenant application",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": [],
            "expected_symbols": [],
            "category": "repo_concept"
        },

        # Cross-cutting queries
        {
            "query": "WebSocket consumer for real-time updates",
            "expected_repos": [],
            "expected_files": ["consumer", "websocket"],
            "expected_symbols": ["Consumer"],
            "category": "tech_pattern"
        },
        {
            "query": "database migration",
            "expected_repos": [],
            "expected_files": ["migration", "migrations"],
            "expected_symbols": [],
            "category": "tech_pattern"
        },
    ]


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="V4 Search Quality Evaluation")
    parser.add_argument("--questions", help="Path to questions JSON file")
    parser.add_argument("--levels", nargs="+", default=["symbol", "file", "module", "repo"])
    parser.add_argument("--output", help="Output file path")
    args = parser.parse_args()

    questions = load_eval_questions(args.questions)
    evaluator = SearchQualityEvaluator()

    results = await evaluator.run_eval_suite(questions, args.levels)

    # Save results
    output_file = args.output or f"evals/search_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    Path(output_file).parent.mkdir(exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to: {output_file}")
    return results


if __name__ == "__main__":
    asyncio.run(main())
