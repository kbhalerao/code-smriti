"""
Baseline Evaluation Script - Run before re-embedding
Uses actual API endpoints to evaluate search quality
"""
import json
import asyncio
import httpx
from typing import List, Dict, Any
from loguru import logger

# Load evaluation questions
with open('search_eval_questions.json', 'r') as f:
    eval_data = json.load(f)

questions = eval_data['questions']

async def search_via_api(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Call the actual chat/test API to get search results"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                'http://localhost:8000/api/chat/test',
                json={'query': query}
            )
            if response.status_code == 200:
                data = response.json()
                return data.get('sources', [])
            else:
                logger.error(f"Search failed: {response.status_code}")
                return []
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []

def calculate_metrics(results: List[Dict], expected_files: List[str]) -> Dict:
    """Calculate search quality metrics"""
    # Extract file paths from results
    result_files = [r.get('file', '') for r in results]

    # Find rank of first expected file
    reciprocal_rank = 0.0
    for i, result_file in enumerate(result_files):
        for expected_file in expected_files:
            # Match if expected file is contained in result file path
            if expected_file in result_file:
                reciprocal_rank = 1.0 / (i + 1)
                break
        if reciprocal_rank > 0:
            break

    # Precision@K
    def precision_at_k(k: int) -> float:
        top_k = result_files[:k]
        matches = 0
        for result_file in top_k:
            for expected_file in expected_files:
                if expected_file in result_file:
                    matches += 1
                    break
        return matches / k if k > 0 else 0.0

    return {
        'reciprocal_rank': reciprocal_rank,
        'precision_at_5': precision_at_k(5),
        'precision_at_10': precision_at_k(10),
        'found': reciprocal_rank > 0
    }

async def run_evaluation():
    """Run full evaluation suite"""
    logger.info(f"Starting baseline evaluation ({len(questions)} questions)")

    results = []

    for i, q in enumerate(questions, 1):
        query = q['query']
        expected_files = q['expected_files']

        logger.info(f"[{i}/{len(questions)}] {query}")

        # Search
        search_results = await search_via_api(query, limit=10)

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
                    'file': r.get('file', ''),
                    'repo': r.get('repo', ''),
                    'score': r.get('score', 0.0)
                }
                for r in search_results[:5]
            ],
            **metrics
        }

        results.append(result)

        logger.info(f"  RR: {metrics['reciprocal_rank']:.3f}, P@5: {metrics['precision_at_5']:.3f}, Found: {metrics['found']}")

        # Small delay to avoid overwhelming API
        await asyncio.sleep(0.5)

    # Aggregate metrics
    total = len(results)
    avg_rr = sum(r['reciprocal_rank'] for r in results) / total
    avg_p5 = sum(r['precision_at_5'] for r in results) / total
    avg_p10 = sum(r['precision_at_10'] for r in results) / total
    success_rate = sum(1 for r in results if r['found']) / total

    # By category
    categories = {}
    for r in results:
        cat = r['category']
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(r)

    category_metrics = {}
    for cat, cat_results in categories.items():
        cat_total = len(cat_results)
        category_metrics[cat] = {
            'count': cat_total,
            'mrr': sum(r['reciprocal_rank'] for r in cat_results) / cat_total,
            'p5': sum(r['precision_at_5'] for r in cat_results) / cat_total,
            'p10': sum(r['precision_at_10'] for r in cat_results) / cat_total,
            'success_rate': sum(1 for r in cat_results if r['found']) / cat_total
        }

    # By difficulty
    difficulties = {}
    for r in results:
        diff = r['difficulty']
        if diff not in difficulties:
            difficulties[diff] = []
        difficulties[diff].append(r)

    difficulty_metrics = {}
    for diff, diff_results in difficulties.items():
        diff_total = len(diff_results)
        difficulty_metrics[diff] = {
            'count': diff_total,
            'mrr': sum(r['reciprocal_rank'] for r in diff_results) / diff_total,
            'p5': sum(r['precision_at_5'] for r in diff_results) / diff_total,
            'p10': sum(r['precision_at_10'] for r in diff_results) / diff_total,
            'success_rate': sum(1 for r in diff_results if r['found']) / diff_total
        }

    output = {
        'timestamp': '2025-11-19-baseline',
        'phase': 'BEFORE re-embedding (all-mpnet-base-v2)',
        'overall': {
            'total_questions': total,
            'mean_reciprocal_rank': avg_rr,
            'avg_precision_at_5': avg_p5,
            'avg_precision_at_10': avg_p10,
            'success_rate': success_rate
        },
        'by_category': category_metrics,
        'by_difficulty': difficulty_metrics,
        'detailed_results': results
    }

    # Save results
    output_file = '/tmp/eval_before_reembed.json'
    with open(output_file, 'w') as f:
        json.dump(output, f, indent=2)

    logger.info(f"\n{'='*70}")
    logger.info("BASELINE EVALUATION RESULTS (BEFORE RE-EMBEDDING)")
    logger.info(f"{'='*70}")
    logger.info(f"Total Questions: {total}")
    logger.info(f"Mean Reciprocal Rank: {avg_rr:.3f}")
    logger.info(f"Avg Precision@5: {avg_p5:.3f}")
    logger.info(f"Avg Precision@10: {avg_p10:.3f}")
    logger.info(f"Success Rate: {success_rate:.1%}")
    logger.info(f"\nBy Category:")
    for cat, metrics in sorted(category_metrics.items()):
        logger.info(f"  {cat}: MRR={metrics['mrr']:.3f}, P@5={metrics['p5']:.3f}, Success={metrics['success_rate']:.1%}")
    logger.info(f"\nBy Difficulty:")
    for diff, metrics in sorted(difficulty_metrics.items()):
        logger.info(f"  {diff}: MRR={metrics['mrr']:.3f}, P@5={metrics['p5']:.3f}, Success={metrics['success_rate']:.1%}")
    logger.info(f"\nResults saved to: {output_file}")
    logger.info(f"{'='*70}")

if __name__ == '__main__':
    asyncio.run(run_evaluation())
