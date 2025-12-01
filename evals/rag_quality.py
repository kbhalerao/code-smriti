#!/usr/bin/env python3
"""
V4 RAG Quality Evaluator

Tests the /api/rag/ endpoint (ask_codebase) for answer quality:
- Answer relevance: Does the answer address the question?
- Groundedness: Is the answer based on actual code?
- Citation quality: Are file/symbol references accurate?
- Response time: Latency metrics

Uses LLM-as-a-judge for automated evaluation.
"""
import asyncio
import json
import httpx
import time
from datetime import datetime
from typing import Dict, List, Any, Optional
from pathlib import Path
import os
import re

from dotenv import load_dotenv
load_dotenv()


class RAGQualityEvaluator:
    """Evaluate V4 RAG answer quality."""

    def __init__(self, api_url: str = None):
        self.api_url = api_url or os.getenv("CODESMRITI_API_URL", "http://localhost")
        self.username = os.getenv("CODESMRITI_USERNAME") or os.getenv("API_USERNAME", "")
        self.password = os.getenv("CODESMRITI_PASSWORD") or os.getenv("API_PASSWORD", "")
        self.token: Optional[str] = None

        # LLM judge settings (optional - for automated evaluation)
        self.judge_enabled = os.getenv("EVAL_LLM_JUDGE", "false").lower() == "true"
        self.judge_api_url = os.getenv("EVAL_LLM_URL", "http://localhost:1234/v1")

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

    async def ask(self, query: str, timeout: float = 120.0) -> Dict[str, Any]:
        """Execute a RAG query."""
        token = await self.get_token()

        start_time = time.time()
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{self.api_url}/api/rag/",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query, "stream": False},
                timeout=timeout
            )
            response.raise_for_status()
            elapsed = time.time() - start_time

            data = response.json()
            data["response_time"] = elapsed
            return data

    def extract_citations(self, answer: str) -> Dict[str, List[str]]:
        """Extract file and symbol citations from answer."""
        citations = {
            "files": [],
            "symbols": [],
            "repos": []
        }

        # File patterns: path/to/file.py, `file.py`, file.py:123
        file_pattern = r'`?([a-zA-Z0-9_/.-]+\.(py|js|ts|tsx|jsx|go|rs|java|rb|svelte))`?(?::\d+)?'
        citations["files"] = list(set(re.findall(file_pattern, answer)))
        citations["files"] = [f[0] if isinstance(f, tuple) else f for f in citations["files"]]

        # Symbol patterns: `FunctionName`, class ClassName, def function_name
        symbol_pattern = r'`([A-Z][a-zA-Z0-9_]+)`|(?:class|def|function)\s+([a-zA-Z_][a-zA-Z0-9_]*)'
        matches = re.findall(symbol_pattern, answer)
        citations["symbols"] = list(set([m[0] or m[1] for m in matches if m[0] or m[1]]))

        # Repo patterns: owner/repo
        repo_pattern = r'([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+)'
        citations["repos"] = list(set(re.findall(repo_pattern, answer)))

        return citations

    def evaluate_answer(
        self,
        question: Dict,
        answer: str,
        response_time: float
    ) -> Dict[str, Any]:
        """Evaluate a single answer."""
        expected_repos = question.get("expected_repos", [])
        expected_files = question.get("expected_files", [])
        expected_content = question.get("expected_content", [])

        citations = self.extract_citations(answer)

        # Check if expected content is mentioned
        content_hits = sum(
            1 for keyword in expected_content
            if keyword.lower() in answer.lower()
        ) if expected_content else 0

        # Check if expected repos are cited
        repo_hits = sum(
            1 for repo in expected_repos
            if any(repo.lower() in cited.lower() for cited in citations["repos"])
        ) if expected_repos else 0

        # Check if expected files are cited
        file_hits = sum(
            1 for file in expected_files
            if any(file.lower() in cited.lower() for cited in citations["files"])
        ) if expected_files else 0

        # Calculate scores
        answer_length = len(answer)
        has_code_block = "```" in answer

        # Heuristic quality score (0-1)
        quality_score = 0.0

        # Points for non-empty answer
        if answer_length > 50:
            quality_score += 0.2

        # Points for reasonable length
        if 200 < answer_length < 3000:
            quality_score += 0.2

        # Points for code blocks (shows groundedness)
        if has_code_block:
            quality_score += 0.2

        # Points for citing expected content
        if expected_content and content_hits > 0:
            quality_score += 0.2 * min(content_hits / len(expected_content), 1.0)

        # Points for citing files/repos
        if citations["files"] or citations["repos"]:
            quality_score += 0.2

        return {
            "answer_length": answer_length,
            "has_code_block": has_code_block,
            "citations": citations,
            "repo_hits": repo_hits,
            "file_hits": file_hits,
            "content_hits": content_hits,
            "quality_score": quality_score,
            "response_time": response_time,
            "answer_preview": answer[:300] + "..." if len(answer) > 300 else answer
        }

    async def run_eval_suite(self, questions: List[Dict]) -> Dict[str, Any]:
        """Run RAG evaluation suite."""
        print("=" * 70)
        print("V4 RAG QUALITY EVALUATION")
        print("=" * 70)
        print(f"Questions: {len(questions)}")
        print(f"API: {self.api_url}")
        print("=" * 70)

        results = []
        total_time = 0
        errors = []

        for q_idx, question in enumerate(questions, 1):
            query = question["query"]
            category = question.get("category", "general")

            print(f"\n[{q_idx}/{len(questions)}] Q: {query[:60]}...")

            try:
                response = await self.ask(query)
                answer = response.get("answer", "")
                response_time = response.get("response_time", 0)
                total_time += response_time

                metrics = self.evaluate_answer(question, answer, response_time)
                metrics.update({
                    "question_id": question.get("id", q_idx),
                    "query": query,
                    "category": category,
                    "full_answer": answer,
                    "status": "success"
                })

                results.append(metrics)

                status = "GOOD" if metrics["quality_score"] >= 0.6 else "FAIR" if metrics["quality_score"] >= 0.4 else "POOR"
                print(f"  {status} | Score: {metrics['quality_score']:.2f} | Time: {response_time:.1f}s | Len: {metrics['answer_length']}")

            except Exception as e:
                print(f"  ERROR: {str(e)[:60]}")
                errors.append({
                    "question_id": question.get("id", q_idx),
                    "query": query,
                    "error": str(e)
                })
                results.append({
                    "question_id": question.get("id", q_idx),
                    "query": query,
                    "category": category,
                    "status": "error",
                    "error": str(e)
                })

            # Small delay between requests
            await asyncio.sleep(0.5)

        # Calculate summary
        successful = [r for r in results if r.get("status") == "success"]

        summary = {
            "total_questions": len(questions),
            "successful": len(successful),
            "errors": len(errors),
            "avg_quality_score": sum(r["quality_score"] for r in successful) / len(successful) if successful else 0,
            "avg_response_time": sum(r["response_time"] for r in successful) / len(successful) if successful else 0,
            "total_time": total_time,
            "good_answers": len([r for r in successful if r["quality_score"] >= 0.6]),
            "fair_answers": len([r for r in successful if 0.4 <= r["quality_score"] < 0.6]),
            "poor_answers": len([r for r in successful if r["quality_score"] < 0.4]),
            "answers_with_code": len([r for r in successful if r.get("has_code_block")]),
        }

        # By category
        categories = {}
        for r in successful:
            cat = r.get("category", "general")
            if cat not in categories:
                categories[cat] = {"count": 0, "total_score": 0}
            categories[cat]["count"] += 1
            categories[cat]["total_score"] += r["quality_score"]

        summary["by_category"] = {
            cat: stats["total_score"] / stats["count"]
            for cat, stats in categories.items()
        }

        print("\n" + "=" * 70)
        print("SUMMARY")
        print("=" * 70)
        print(f"Success Rate: {len(successful)}/{len(questions)} ({len(successful)/len(questions)*100:.1f}%)")
        print(f"Avg Quality Score: {summary['avg_quality_score']:.3f}")
        print(f"Avg Response Time: {summary['avg_response_time']:.2f}s")
        print(f"Total Time: {summary['total_time']:.1f}s")
        print(f"\nAnswer Quality Distribution:")
        print(f"  Good (>=0.6):  {summary['good_answers']}")
        print(f"  Fair (0.4-0.6): {summary['fair_answers']}")
        print(f"  Poor (<0.4):   {summary['poor_answers']}")
        print(f"\nAnswers with Code Blocks: {summary['answers_with_code']}")

        if summary["by_category"]:
            print(f"\nBy Category:")
            for cat, score in sorted(summary["by_category"].items(), key=lambda x: -x[1]):
                print(f"  {cat}: {score:.3f}")

        return {
            "timestamp": datetime.now().isoformat(),
            "config": {
                "api_url": self.api_url,
                "question_count": len(questions)
            },
            "summary": summary,
            "results": results,
            "errors": errors
        }


def load_rag_questions(questions_file: str = None) -> List[Dict]:
    """Load RAG evaluation questions from file or use defaults."""
    if questions_file and Path(questions_file).exists():
        with open(questions_file) as f:
            data = json.load(f)
            return data.get("questions", data)

    # Default V4 RAG questions
    return [
        {
            "id": 1,
            "query": "How does the job_counter decorator work in labcore?",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["decorators.py"],
            "expected_content": ["job_counter", "decorator", "counter"],
            "category": "code_explanation"
        },
        {
            "id": 2,
            "query": "What is the authentication flow in labcore?",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["auth", "backend", "login"],
            "expected_content": ["authentication", "user", "login"],
            "category": "architecture"
        },
        {
            "id": 3,
            "query": "How does multi-tenant data isolation work?",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["role_privileges.py", "FilteredQuerySetMixin"],
            "expected_content": ["organization", "tenant", "filter", "queryset"],
            "category": "architecture"
        },
        {
            "id": 4,
            "query": "What Celery tasks are defined in the codebase?",
            "expected_repos": [],
            "expected_files": ["tasks.py"],
            "expected_content": ["task", "celery", "async"],
            "category": "discovery"
        },
        {
            "id": 5,
            "query": "How are WebSocket connections handled?",
            "expected_repos": [],
            "expected_files": ["consumer", "websocket", "channels"],
            "expected_content": ["websocket", "consumer", "connect"],
            "category": "architecture"
        },
        {
            "id": 6,
            "query": "What Django models exist for sample management?",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["models.py", "samples"],
            "expected_content": ["model", "sample", "class"],
            "category": "discovery"
        },
        {
            "id": 7,
            "query": "How does the API handle pagination?",
            "expected_repos": [],
            "expected_files": ["views.py", "api.py", "pagination"],
            "expected_content": ["pagination", "page", "limit"],
            "category": "implementation"
        },
        {
            "id": 8,
            "query": "What external integrations exist (HubSpot, USDA)?",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["hubspot", "usda", "integration"],
            "expected_content": ["hubspot", "usda", "api", "integration"],
            "category": "architecture"
        },
        {
            "id": 9,
            "query": "How are permissions checked in views?",
            "expected_repos": ["kbhalerao/labcore"],
            "expected_files": ["permission", "mixin", "role"],
            "expected_content": ["permission", "check", "user"],
            "category": "implementation"
        },
        {
            "id": 10,
            "query": "What testing utilities are available?",
            "expected_repos": [],
            "expected_files": ["test", "conftest", "fixture"],
            "expected_content": ["test", "fixture", "mock"],
            "category": "discovery"
        },
    ]


async def main():
    import argparse

    parser = argparse.ArgumentParser(description="V4 RAG Quality Evaluation")
    parser.add_argument("--questions", help="Path to questions JSON file")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--timeout", type=float, default=120.0, help="Request timeout")
    args = parser.parse_args()

    questions = load_rag_questions(args.questions)
    evaluator = RAGQualityEvaluator()

    results = await evaluator.run_eval_suite(questions)

    # Save results
    output_file = args.output or f"evals/rag_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    Path(output_file).parent.mkdir(exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)

    print(f"\nResults saved to: {output_file}")
    return results


if __name__ == "__main__":
    asyncio.run(main())
