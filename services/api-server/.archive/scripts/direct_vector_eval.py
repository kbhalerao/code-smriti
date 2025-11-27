"""
Direct vector search evaluation - bypasses API, queries Couchbase directly
"""

import json
import asyncio
from typing import List, Dict, Any
from sentence_transformers import SentenceTransformer
from loguru import logger

from app.database.couchbase_client import CouchbaseClient


def load_eval_questions(file_path: str) -> List[Dict[str, Any]]:
    """Load evaluation questions from JSON file"""
    with open(file_path, 'r') as f:
        data = json.load(f)
        # Handle both array format and object with 'questions' key
        if isinstance(data, list):
            return data
        elif isinstance(data, dict) and 'questions' in data:
            return data['questions']
        else:
            raise ValueError("Invalid eval questions format")


async def vector_search_direct(
    db: CouchbaseClient,
    model: SentenceTransformer,
    query_text: str,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Perform direct vector search on Couchbase using FTS index
    Bypasses the API layer completely
    """

    # Generate query embedding
    query_embedding = model.encode(
        f"search_query: {query_text}",
        convert_to_tensor=False,
        normalize_embeddings=True
    )

    # Direct FTS vector search using search.Search
    import couchbase.search as search
    from couchbase.options import SearchOptions
    from couchbase.vector_search import VectorQuery, VectorSearch

    # Create vector query
    vector_query = VectorQuery(
        'embedding_vector',  # FTS index field name
        query_embedding.tolist(),
        num_candidates=limit * 3  # Search more candidates
    )

    # Create vector search request
    vector_search = VectorSearch.from_vector_query(vector_query)

    # Execute search
    try:
        search_result = db.cluster.search_query(
            'code_kosha_vector_index',  # FTS index name
            search.MatchNoneQuery(),  # Don't filter by text
            SearchOptions(
                vector_search=vector_search,
                limit=limit,
                fields=['*']  # Return all fields
            )
        )

        results = []
        for row in search_result.rows():
            doc = row.fields
            results.append({
                'chunk_id': doc.get('chunk_id', 'unknown'),
                'file_path': doc.get('file_path', 'unknown'),
                'repo_id': doc.get('repo_id', 'unknown'),
                'type': doc.get('type', 'unknown'),
                'score': row.score,
                'code_text': doc.get('code_text', doc.get('content', doc.get('commit_message', '')))[:200]
            })

        return results

    except Exception as e:
        logger.error(f"Vector search failed: {e}")
        return []


def evaluate_results(
    question: Dict[str, Any],
    results: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Evaluate search results quality
    """
    expected_files = set(question.get('expected_files', []))
    repo = question.get('repo', '')

    # Check if any expected files appear in results
    found_files = set()
    found_positions = []

    for i, result in enumerate(results):
        file_path = result.get('file_path', '')

        # Check if this file matches any expected file
        for expected in expected_files:
            if expected in file_path:
                found_files.add(expected)
                found_positions.append(i + 1)  # 1-indexed position

    # Calculate metrics
    precision_at_k = len(found_files) / min(len(results), 10) if results else 0
    recall = len(found_files) / len(expected_files) if expected_files else 0

    # MRR: 1/rank of first correct result
    mrr = 1.0 / found_positions[0] if found_positions else 0.0

    return {
        'expected_files': list(expected_files),
        'found_files': list(found_files),
        'found_at_positions': found_positions,
        'total_results': len(results),
        'precision@10': precision_at_k,
        'recall': recall,
        'mrr': mrr,
        'success': len(found_files) > 0
    }


async def run_full_evaluation():
    """Run complete evaluation suite"""

    logger.info("=" * 80)
    logger.info("DIRECT VECTOR SEARCH EVALUATION")
    logger.info("=" * 80)

    # Load model
    logger.info("Loading nomic-ai/nomic-embed-text-v1.5...")
    model = SentenceTransformer(
        'nomic-ai/nomic-embed-text-v1.5',
        trust_remote_code=True
    )
    logger.info(f"✓ Model loaded (device: {model.device})")

    # Connect to database
    db = CouchbaseClient()
    logger.info("✓ Connected to Couchbase")

    # Load evaluation questions
    questions = load_eval_questions('search_eval_questions.json')
    logger.info(f"✓ Loaded {len(questions)} evaluation questions")

    # Run evaluations
    results = []
    successes = 0
    total_mrr = 0.0
    total_precision = 0.0
    total_recall = 0.0

    for i, question in enumerate(questions, 1):
        query = question['query']
        category = question.get('category', 'unknown')

        logger.info(f"\n[{i}/{len(questions)}] {category}: {query}")

        # Perform vector search
        search_results = await vector_search_direct(db, model, query, limit=10)

        # Evaluate results
        eval_result = evaluate_results(question, search_results)

        # Log summary
        if eval_result['success']:
            logger.info(f"  ✓ Found {len(eval_result['found_files'])}/{len(eval_result['expected_files'])} expected files")
            logger.info(f"  Positions: {eval_result['found_at_positions']}")
            successes += 1
        else:
            logger.warning(f"  ✗ No expected files found")
            logger.warning(f"  Expected: {eval_result['expected_files']}")
            if search_results:
                logger.warning(f"  Top result: {search_results[0]['file_path']}")

        total_mrr += eval_result['mrr']
        total_precision += eval_result['precision@10']
        total_recall += eval_result['recall']

        results.append({
            'question': question,
            'search_results': search_results[:3],  # Save top 3 only
            'evaluation': eval_result
        })

    # Calculate aggregate metrics
    avg_mrr = total_mrr / len(questions)
    avg_precision = total_precision / len(questions)
    avg_recall = total_recall / len(questions)
    success_rate = successes / len(questions)

    logger.info("\n" + "=" * 80)
    logger.info("EVALUATION SUMMARY")
    logger.info("=" * 80)
    logger.info(f"Total questions: {len(questions)}")
    logger.info(f"Successful: {successes} ({success_rate*100:.1f}%)")
    logger.info(f"Mean Reciprocal Rank (MRR): {avg_mrr:.3f}")
    logger.info(f"Average Precision@10: {avg_precision:.3f}")
    logger.info(f"Average Recall: {avg_recall:.3f}")

    # Save detailed results
    output_file = '/tmp/direct_vector_eval_results.json'
    with open(output_file, 'w') as f:
        json.dump({
            'summary': {
                'total_questions': len(questions),
                'successful': successes,
                'success_rate': success_rate,
                'mrr': avg_mrr,
                'precision@10': avg_precision,
                'recall': avg_recall
            },
            'results': results
        }, f, indent=2, default=str)

    logger.info(f"\n✓ Detailed results saved to: {output_file}")

    return {
        'success_rate': success_rate,
        'mrr': avg_mrr,
        'precision': avg_precision,
        'recall': avg_recall
    }


if __name__ == "__main__":
    asyncio.run(run_full_evaluation())
