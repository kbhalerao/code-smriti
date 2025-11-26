#!/usr/bin/env python3
"""
Evaluate search API quality with the 37-question eval suite.

This script tests ONLY the search endpoint (/api/chat/search), not the full RAG.
Goal: Verify search results are relevant and usable by LLM before testing narrative generation.
"""
import asyncio
import json
import httpx
from datetime import datetime
from typing import List, Dict, Any
from pathlib import Path


async def run_search_query(client: httpx.AsyncClient, query: str, limit: int = 10) -> Dict[str, Any]:
    """Run a single search query and return results"""
    try:
        response = await client.post(
            'http://localhost:8000/api/chat/search',
            json={
                "query": query,
                "limit": limit,
                "doc_type": "code_chunk"
            },
            timeout=30.0
        )

        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "results": []}

        data = response.json()
        return {
            "results": data.get('results', []),
            "count": data.get('count', 0),
            "search_mode": data.get('search_mode', 'unknown')
        }

    except Exception as e:
        return {"error": str(e), "results": []}


def evaluate_search_results(
    results: List[Dict],
    expected_repos: List[str],
    expected_files: List[str],
    query: str
) -> Dict[str, Any]:
    """Evaluate search result quality"""

    if not results:
        return {
            "found_any": False,
            "found_expected_repo": False,
            "found_expected_file": False,
            "repo_precision": 0.0,
            "file_precision": 0.0,
            "avg_score": 0.0,
            "top_score": 0.0,
            "usable_for_llm": False,
            "diversity_score": 0.0
        }

    # Extract repo and file info
    result_repos = [r.get('repo_id', '') for r in results]
    result_files = [r.get('file_path', '') for r in results]
    scores = [r.get('score', 0.0) for r in results]

    # Check expected repos
    found_expected_repo = any(
        any(exp_repo in repo for exp_repo in expected_repos)
        for repo in result_repos
    )

    # Check expected files (flexible matching)
    found_expected_file = any(
        any(exp_file in file for exp_file in expected_files)
        for file in result_files
    )

    # Repo precision: % of results from expected repos
    repo_matches = sum(
        1 for repo in result_repos
        if any(exp_repo in repo for exp_repo in expected_repos)
    )
    repo_precision = repo_matches / len(results) if results else 0.0

    # File precision: % of results with expected files
    file_matches = sum(
        1 for file in result_files
        if any(exp_file in file for exp_file in expected_files)
    )
    file_precision = file_matches / len(results) if results else 0.0

    # Score stats
    avg_score = sum(scores) / len(scores) if scores else 0.0
    top_score = max(scores) if scores else 0.0

    # Diversity: unique repos and files
    unique_repos = len(set(result_repos))
    unique_files = len(set(result_files))
    diversity_score = (unique_repos + unique_files) / (2 * len(results)) if results else 0.0

    # LLM usability: good if we have relevant results with decent scores
    usable_for_llm = (
        len(results) >= 3 and
        avg_score >= 0.5 and
        (found_expected_repo or found_expected_file or repo_precision >= 0.3)
    )

    return {
        "found_any": len(results) > 0,
        "found_expected_repo": found_expected_repo,
        "found_expected_file": found_expected_file,
        "repo_precision": repo_precision,
        "file_precision": file_precision,
        "avg_score": avg_score,
        "top_score": top_score,
        "usable_for_llm": usable_for_llm,
        "diversity_score": diversity_score,
        "unique_repos": unique_repos,
        "unique_files": unique_files,
        "total_results": len(results)
    }


async def run_eval_suite(eval_file: str, output_file: str, limit: int = 10):
    """Run the full eval suite"""

    # Load questions
    with open(eval_file, 'r') as f:
        eval_data = json.load(f)

    questions = eval_data['questions']
    total = len(questions)

    print("=" * 80)
    print("SEARCH API EVALUATION")
    print("=" * 80)
    print(f"Total questions: {total}")
    print(f"Results per query: {limit}")
    print(f"Endpoint: /api/chat/search")
    print("=" * 80)
    print()

    results_summary = []
    categories = {}

    async with httpx.AsyncClient() as client:
        for i, q in enumerate(questions, 1):
            query = q['query']
            expected_repos = q.get('expected_repos', [])
            expected_files = q.get('expected_files', [])
            category = q.get('category', 'unknown')
            difficulty = q.get('difficulty', 'unknown')

            print(f"[{i}/{total}] Q{q['id']}: {query[:60]}...")

            # Run search
            search_result = await run_search_query(client, query, limit)

            if 'error' in search_result:
                print(f"  ❌ Error: {search_result['error']}")
                eval_metrics = {"found_any": False, "usable_for_llm": False}
            else:
                results = search_result['results']
                eval_metrics = evaluate_search_results(
                    results, expected_repos, expected_files, query
                )

                # Print quick summary
                status = "✅" if eval_metrics['usable_for_llm'] else "⚠️"
                print(f"  {status} Results: {eval_metrics['total_results']}, "
                      f"Avg Score: {eval_metrics['avg_score']:.3f}, "
                      f"Repo Match: {eval_metrics['found_expected_repo']}, "
                      f"File Match: {eval_metrics['found_expected_file']}")

            # Store results
            result_record = {
                "question_id": q['id'],
                "query": query,
                "category": category,
                "difficulty": difficulty,
                "expected_repos": expected_repos,
                "expected_files": expected_files,
                "search_count": search_result.get('count', 0),
                "metrics": eval_metrics,
                "top_results": [
                    {
                        "repo": r.get('repo_id'),
                        "file": r.get('file_path'),
                        "score": r.get('score', 0)
                    }
                    for r in search_result.get('results', [])[:5]
                ]
            }
            results_summary.append(result_record)

            # Track by category
            if category not in categories:
                categories[category] = {"total": 0, "usable": 0}
            categories[category]["total"] += 1
            if eval_metrics.get('usable_for_llm', False):
                categories[category]["usable"] += 1

            # Small delay to not hammer the API
            await asyncio.sleep(0.1)

    # Overall statistics
    print("\n" + "=" * 80)
    print("OVERALL RESULTS")
    print("=" * 80)

    total_usable = sum(1 for r in results_summary if r['metrics'].get('usable_for_llm', False))
    total_found_repo = sum(1 for r in results_summary if r['metrics'].get('found_expected_repo', False))
    total_found_file = sum(1 for r in results_summary if r['metrics'].get('found_expected_file', False))

    avg_repo_precision = sum(r['metrics'].get('repo_precision', 0) for r in results_summary) / total
    avg_file_precision = sum(r['metrics'].get('file_precision', 0) for r in results_summary) / total
    avg_score = sum(r['metrics'].get('avg_score', 0) for r in results_summary) / total
    avg_diversity = sum(r['metrics'].get('diversity_score', 0) for r in results_summary) / total

    print(f"\nUsability for LLM: {total_usable}/{total} ({total_usable/total*100:.1f}%)")
    print(f"Found Expected Repo: {total_found_repo}/{total} ({total_found_repo/total*100:.1f}%)")
    print(f"Found Expected File: {total_found_file}/{total} ({total_found_file/total*100:.1f}%)")
    print(f"\nAverage Metrics:")
    print(f"  Repo Precision: {avg_repo_precision:.3f}")
    print(f"  File Precision: {avg_file_precision:.3f}")
    print(f"  Score: {avg_score:.3f}")
    print(f"  Diversity: {avg_diversity:.3f}")

    print(f"\nBy Category:")
    for cat, stats in sorted(categories.items()):
        pct = stats['usable'] / stats['total'] * 100 if stats['total'] > 0 else 0
        print(f"  {cat:20s}: {stats['usable']:2d}/{stats['total']:2d} ({pct:5.1f}%)")

    # Save results
    output = {
        "metadata": {
            "timestamp": datetime.now().isoformat(),
            "total_questions": total,
            "limit_per_query": limit,
            "endpoint": "/api/chat/search"
        },
        "summary": {
            "total_usable": total_usable,
            "total_found_repo": total_found_repo,
            "total_found_file": total_found_file,
            "avg_repo_precision": avg_repo_precision,
            "avg_file_precision": avg_file_precision,
            "avg_score": avg_score,
            "avg_diversity": avg_diversity,
            "by_category": categories
        },
        "results": results_summary
    }

    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Results saved to: {output_file}")
    print("=" * 80)


if __name__ == "__main__":
    eval_file = "tests/search_eval_questions.json"
    output_file = f"evals/search_eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    # Ensure evals directory exists
    Path("evals").mkdir(exist_ok=True)

    asyncio.run(run_eval_suite(eval_file, output_file, limit=10))
