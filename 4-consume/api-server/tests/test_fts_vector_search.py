#!/usr/bin/env python3
"""
Test FTS vector search with Couchbase
"""
import json
import sys
import os
from typing import List, Dict, Any

from sentence_transformers import SentenceTransformer
from couchbase.search import SearchOptions, SearchRequest
from couchbase.vector_search import VectorQuery, VectorSearch

# Add parent directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../lib/ingestion-worker'))

from app.database.couchbase_client import CouchbaseClient


def load_evaluation_questions(filepath: str) -> List[Dict[str, Any]]:
    """Load evaluation questions from JSON file"""
    with open(filepath, 'r') as f:
        data = json.load(f)
    return data['questions']


def generate_embedding(model: SentenceTransformer, text: str) -> List[float]:
    """Generate embedding for a query"""
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def vector_search_fts(
    cluster,
    index_name: str,
    query_embedding: List[float],
    top_k: int = 10
) -> List[Dict[str, Any]]:
    """Perform vector search using Couchbase FTS"""
    # Create vector query
    vector_query = VectorQuery(
        "embedding",  # field name in the index
        query_embedding,
        num_candidates=top_k
    )

    # Create vector search
    vector_search = VectorSearch.from_vector_query(vector_query)

    # Execute search
    search_req = SearchRequest.create(vector_search)
    options = SearchOptions(limit=top_k)

    result = cluster.search(
        index_name,
        search_req,
        options
    )

    # Parse results and fetch full documents
    results = []
    collection = cluster.bucket('code_kosha').default_collection()

    for row in result.rows():
        # Fetch full document
        try:
            doc = collection.get(row.id)
            doc_data = doc.content_as[dict]

            results.append({
                'doc_id': row.id,
                'score': row.score,
                'repo_id': doc_data.get('repo_id', ''),
                'file_path': doc_data.get('file_path', ''),
                'content': doc_data.get('content', ''),
                'chunk_type': doc_data.get('chunk_type', '')
            })
        except Exception as e:
            print(f"Warning: Could not fetch document {row.id}: {e}")
            continue

    return results


def evaluate_single_query(
    question: Dict[str, Any],
    results: List[Dict[str, Any]],
    k_values: List[int] = [1, 3, 5, 10]
) -> Dict[str, Any]:
    """Evaluate results for a single query"""
    expected_repos = set(question['expected_repos'])
    expected_files = set(question['expected_files'])

    # Extract results
    result_repos = [r.get('repo_id', '') for r in results]
    result_files = [r.get('file_path', '') for r in results]

    # Calculate metrics
    metrics = {
        'question_id': question['id'],
        'query': question['query'],
        'category': question['category'],
        'difficulty': question['difficulty']
    }

    # Recall@K for repos
    for k in k_values:
        top_k_repos = set(result_repos[:k])
        recall = len(expected_repos.intersection(top_k_repos)) / len(expected_repos) if expected_repos else 0
        metrics[f'repo_recall@{k}'] = recall

    # Recall@K for files
    for k in k_values:
        top_k_files = result_files[:k]
        found_files = []
        for expected in expected_files:
            expected_name = expected.split('/')[-1]
            for result_file in top_k_files:
                if expected_name in result_file or expected in result_file:
                    found_files.append(expected)
                    break

        recall = len(found_files) / len(expected_files) if expected_files else 0
        metrics[f'file_recall@{k}'] = recall

    # Find rank of first correct result
    first_correct_rank = None
    for i, (repo, filepath) in enumerate(zip(result_repos, result_files)):
        if repo in expected_repos:
            expected_name_matches = any(
                exp.split('/')[-1] in filepath or exp in filepath
                for exp in expected_files
            )
            if expected_name_matches:
                first_correct_rank = i + 1
                break

    metrics['first_correct_rank'] = first_correct_rank
    metrics['mrr'] = 1.0 / first_correct_rank if first_correct_rank else 0.0

    return metrics


def print_detailed_results(question: Dict[str, Any], results: List[Dict[str, Any]], metrics: Dict[str, Any]):
    """Print detailed results for a query"""
    print(f"\n{'='*80}")
    print(f"Query #{question['id']}: {question['query']}")
    print(f"Category: {question['category']} | Difficulty: {question['difficulty']}")
    print(f"Expected repos: {', '.join(question['expected_repos'])}")
    print(f"Expected files: {', '.join(question['expected_files'])}")
    print(f"\nTop Results:")

    for i, result in enumerate(results[:5], 1):
        repo = result.get('repo_id', 'N/A')
        filepath = result.get('file_path', 'N/A')
        score = result.get('score', 0)

        # Check if this result matches expectations
        is_expected_repo = repo in question['expected_repos']
        is_expected_file = any(
            exp.split('/')[-1] in filepath or exp in filepath
            for exp in question['expected_files']
        )
        marker = "✓" if (is_expected_repo and is_expected_file) else "✗"

        print(f"  {i}. {marker} [{repo}] {filepath}")
        print(f"     Score: {score:.4f}")
        if 'content' in result and result['content']:
            preview = result['content'][:150].replace('\n', ' ')
            print(f"     Preview: {preview}...")

    print(f"\nMetrics:")
    print(f"  Repo Recall@5: {metrics['repo_recall@5']:.2%}")
    print(f"  File Recall@5: {metrics['file_recall@5']:.2%}")
    print(f"  MRR: {metrics['mrr']:.4f}")
    print(f"  First correct result rank: {metrics['first_correct_rank'] or 'Not found'}")


def main():
    print("Loading evaluation questions...")
    questions = load_evaluation_questions('search_eval_questions.json')
    print(f"Loaded {len(questions)} questions")

    print("\nInitializing embedding model...")
    model = SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', trust_remote_code=True)

    print("Connecting to Couchbase...")
    db = CouchbaseClient()

    print(f"\n{'='*80}")
    print("Running evaluation on first 5 questions...")
    print(f"{'='*80}")

    all_metrics = []

    # Test first 5 questions
    for i, question in enumerate(questions[:5], 1):
        print(f"\nProcessing question {i}/5...")

        # Generate embedding for query
        query_embedding = generate_embedding(model, question['query'])

        try:
            # Search using FTS
            results = vector_search_fts(
                db.cluster,
                "code_vector_index",
                query_embedding,
                top_k=10
            )

            # Evaluate results
            metrics = evaluate_single_query(question, results)
            all_metrics.append(metrics)

            # Print detailed results
            print_detailed_results(question, results, metrics)

        except Exception as e:
            print(f"Error searching for question {i}: {e}")
            import traceback
            traceback.print_exc()

    # Print summary statistics
    if all_metrics:
        print(f"\n{'='*80}")
        print("SUMMARY STATISTICS (First 5 Questions)")
        print(f"{'='*80}")

        for k in [1, 3, 5, 10]:
            avg_repo_recall = sum(m[f'repo_recall@{k}'] for m in all_metrics) / len(all_metrics)
            avg_file_recall = sum(m[f'file_recall@{k}'] for m in all_metrics) / len(all_metrics)
            print(f"Average Repo Recall@{k}: {avg_repo_recall:.2%}")
            print(f"Average File Recall@{k}: {avg_file_recall:.2%}")

        avg_mrr = sum(m['mrr'] for m in all_metrics) / len(all_metrics)
        print(f"\nAverage MRR: {avg_mrr:.4f}")

        success_count = sum(1 for m in all_metrics if m['first_correct_rank'] and m['first_correct_rank'] <= 5)
        print(f"Questions with correct result in top 5: {success_count}/{len(all_metrics)} ({success_count/len(all_metrics):.1%})")


if __name__ == '__main__':
    main()
