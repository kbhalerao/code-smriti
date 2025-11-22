#!/usr/bin/env python3
"""
Baseline evaluation using direct search function (bypasses HTTP layer)
"""
import json
import asyncio
import time
from datetime import datetime
from typing import List, Dict, Any
from loguru import logger
import httpx

from app.database.couchbase_client import CouchbaseClient
from app.chat.manual_rag_agent import RAGContext, search_code_tool
from sentence_transformers import SentenceTransformer

# Load evaluation questions
with open('tests/search_eval_questions.json', 'r') as f:
    eval_data = json.load(f)

questions = eval_data['questions']

async def search_direct(ctx: RAGContext, query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Call search function directly"""
    try:
        results = await search_code_tool(ctx, query, limit=limit)
        return results
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

def calculate_metrics(results: List[Dict], expected_files: List[str]) -> Dict:
    """Calculate search quality metrics"""
    # Extract file paths from results (search returns 'file_path' not 'file')
    result_files = [r.get('file_path', r.get('file', '')) for r in results]

    # Find rank of first expected file
    reciprocal_rank = 0.0
    for i, result_file in enumerate(result_files):
        if any(exp in result_file for exp in expected_files):
            reciprocal_rank = 1.0 / (i + 1)
            break

    # Precision @ 5
    top_5 = result_files[:5]
    relevant_in_top_5 = sum(1 for f in top_5 if any(exp in f for exp in expected_files))
    precision_at_5 = relevant_in_top_5 / min(5, len(results)) if results else 0.0

    # Found any
    found = any(any(exp in f for exp in expected_files) for f in result_files)

    return {
        'reciprocal_rank': reciprocal_rank,
        'precision_at_5': precision_at_5,
        'found': found
    }

async def run_evaluation():
    """Run baseline evaluation on all questions"""

    # Initialize resources
    logger.info("Initializing resources...")
    db = CouchbaseClient()
    embedding_model = SentenceTransformer(
        "nomic-ai/nomic-embed-text-v1.5",
        trust_remote_code=True
    )

    async with httpx.AsyncClient() as http_client:
        ctx = RAGContext(
            db=db,
            tenant_id="code_kosha",
            ollama_host="http://localhost:11434",
            http_client=http_client,
            embedding_model=embedding_model
        )

        logger.info(f"Starting baseline evaluation ({len(questions)} questions)")

        results = []
        total_rr = 0.0
        total_p5 = 0.0
        found_count = 0

        for i, q in enumerate(questions, 1):
            query = q['query']
            expected_files = q['expected_files']
            expected_repos = q.get('expected_repos', [])
            repo_filter = expected_repos[0] if expected_repos else None

            logger.info(f"[{i}/{len(questions)}] {query} [repo: {repo_filter}]")

            # Search with repo filter (uses N1QL filtering)
            from app.chat.manual_rag_agent import search_code_tool
            search_results = await search_code_tool(ctx, query, limit=10, repo_filter=repo_filter)

            # Calculate metrics
            metrics = calculate_metrics(search_results, expected_files)

            result = {
                'question_id': q['id'],
                'query': query,
                'category': q['category'],
                'difficulty': q['difficulty'],
                'expected_files': expected_files,
                'top_results': [
                    {
                        'file': r.get('file_path', r.get('file', '')),
                        'repo': r.get('repo_id', r.get('repo', '')),
                        'score': r.get('score', 0.0)
                    }
                    for r in search_results[:5]
                ],
                **metrics
            }

            results.append(result)

            total_rr += metrics['reciprocal_rank']
            total_p5 += metrics['precision_at_5']
            if metrics['found']:
                found_count += 1

            logger.info(f"  RR: {metrics['reciprocal_rank']:.3f}, P@5: {metrics['precision_at_5']:.3f}, Found: {metrics['found']}")

        # Overall metrics
        mrr = total_rr / len(questions)
        map_5 = total_p5 / len(questions)
        recall = found_count / len(questions)

        logger.info("\n" + "="*70)
        logger.info("BASELINE EVALUATION RESULTS")
        logger.info("="*70)
        logger.info(f"Total questions: {len(questions)}")
        logger.info(f"MRR (Mean Reciprocal Rank): {mrr:.3f}")
        logger.info(f"MAP@5 (Mean Average Precision @5): {map_5:.3f}")
        logger.info(f"Recall (Found any relevant): {recall:.3f} ({found_count}/{len(questions)})")
        logger.info("="*70)

        # Save results
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = f"/tmp/eval_baseline_{timestamp}.json"

        output = {
            'timestamp': timestamp,
            'total_questions': len(questions),
            'metrics': {
                'mrr': mrr,
                'map_at_5': map_5,
                'recall': recall,
                'found_count': found_count
            },
            'results': results
        }

        with open(output_file, 'w') as f:
            json.dump(output, f, indent=2)

        logger.info(f"\nResults saved to: {output_file}")

        return output

if __name__ == '__main__':
    asyncio.run(run_evaluation())
