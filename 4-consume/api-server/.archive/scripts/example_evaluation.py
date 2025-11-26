#!/usr/bin/env python3
"""
Example code showing how to use the evaluation suite to benchmark search quality.
"""

import json
from pathlib import Path


def load_evaluation_suite(json_file):
    """Load the evaluation suite from JSON file."""
    with open(json_file) as f:
        return json.load(f)


def evaluate_search_results(search_results, expected_files, expected_repos):
    """
    Evaluate search results against expected files and repos.

    Args:
        search_results: List of dicts with 'file_path' and 'repo_id' keys
        expected_files: List of file paths that should be found
        expected_repos: List of repo IDs where files should come from

    Returns:
        dict with evaluation metrics
    """
    metrics = {
        'found_any': False,
        'rank_first_relevant': None,
        'num_relevant_in_top_10': 0,
        'precision_at_5': 0.0,
        'precision_at_10': 0.0,
    }

    relevant_count = 0

    for rank, result in enumerate(search_results[:10], 1):
        # Check if this result is relevant
        is_relevant = False

        # Match by file path (partial match allowed)
        for expected_file in expected_files:
            if expected_file in result.get('file_path', ''):
                is_relevant = True
                break

        # Or match by repo (if file paths not specific)
        if not is_relevant and result.get('repo_id') in expected_repos:
            # More lenient matching for directory/pattern queries
            is_relevant = True

        if is_relevant:
            relevant_count += 1
            metrics['found_any'] = True

            if metrics['rank_first_relevant'] is None:
                metrics['rank_first_relevant'] = rank

            if rank <= 10:
                metrics['num_relevant_in_top_10'] += 1

    # Calculate precision
    if len(search_results) >= 5:
        metrics['precision_at_5'] = relevant_count / min(5, len(search_results))

    if len(search_results) >= 10:
        metrics['precision_at_10'] = relevant_count / min(10, len(search_results))

    return metrics


def run_evaluation_example(eval_suite):
    """
    Example evaluation run.
    Replace mock_search_function with your actual search implementation.
    """

    def mock_search_function(query, limit=10):
        """
        Mock search function - replace this with your actual search.

        Should return list of dicts with:
        - file_path: str
        - repo_id: str
        - score: float (optional)
        - content: str (optional)
        """
        # This is just a placeholder
        # Your real implementation would query the vector database
        return []

    results = {
        'total_questions': 0,
        'questions_answered': 0,
        'mrr_scores': [],
        'precision_at_5': [],
        'precision_at_10': [],
        'by_category': {},
        'by_difficulty': {},
    }

    for question in eval_suite['questions']:
        results['total_questions'] += 1

        # Run search
        search_results = mock_search_function(question['query'], limit=10)

        # Evaluate
        metrics = evaluate_search_results(
            search_results,
            question['expected_files'],
            question['expected_repos']
        )

        # Aggregate metrics
        if metrics['found_any']:
            results['questions_answered'] += 1

        # MRR
        if metrics['rank_first_relevant']:
            mrr = 1.0 / metrics['rank_first_relevant']
        else:
            mrr = 0.0
        results['mrr_scores'].append(mrr)

        # Precision
        results['precision_at_5'].append(metrics['precision_at_5'])
        results['precision_at_10'].append(metrics['precision_at_10'])

        # By category
        category = question['category']
        if category not in results['by_category']:
            results['by_category'][category] = {
                'total': 0,
                'answered': 0,
                'mrr_scores': []
            }
        results['by_category'][category]['total'] += 1
        if metrics['found_any']:
            results['by_category'][category]['answered'] += 1
        results['by_category'][category]['mrr_scores'].append(mrr)

        # By difficulty
        difficulty = question['difficulty']
        if difficulty not in results['by_difficulty']:
            results['by_difficulty'][difficulty] = {
                'total': 0,
                'answered': 0,
                'mrr_scores': []
            }
        results['by_difficulty'][difficulty]['total'] += 1
        if metrics['found_any']:
            results['by_difficulty'][difficulty]['answered'] += 1
        results['by_difficulty'][difficulty]['mrr_scores'].append(mrr)

    # Calculate final metrics
    results['success_rate'] = results['questions_answered'] / results['total_questions']
    results['mrr'] = sum(results['mrr_scores']) / len(results['mrr_scores'])
    results['avg_precision_at_5'] = sum(results['precision_at_5']) / len(results['precision_at_5'])
    results['avg_precision_at_10'] = sum(results['precision_at_10']) / len(results['precision_at_10'])

    # Calculate by category
    for category, stats in results['by_category'].items():
        stats['success_rate'] = stats['answered'] / stats['total']
        stats['mrr'] = sum(stats['mrr_scores']) / len(stats['mrr_scores'])

    # Calculate by difficulty
    for difficulty, stats in results['by_difficulty'].items():
        stats['success_rate'] = stats['answered'] / stats['total']
        stats['mrr'] = sum(stats['mrr_scores']) / len(stats['mrr_scores'])

    return results


def print_results(results):
    """Print evaluation results in a nice format."""

    print("\n" + "="*70)
    print("SEARCH QUALITY EVALUATION RESULTS")
    print("="*70)

    print(f"\nOverall Metrics:")
    print(f"  Questions: {results['total_questions']}")
    print(f"  Success Rate: {results['success_rate']:.1%}")
    print(f"  Mean Reciprocal Rank: {results['mrr']:.3f}")
    print(f"  Avg Precision@5: {results['avg_precision_at_5']:.3f}")
    print(f"  Avg Precision@10: {results['avg_precision_at_10']:.3f}")

    print(f"\nBy Category:")
    for category, stats in sorted(results['by_category'].items()):
        print(f"  {category}:")
        print(f"    Questions: {stats['total']}")
        print(f"    Success Rate: {stats['success_rate']:.1%}")
        print(f"    MRR: {stats['mrr']:.3f}")

    print(f"\nBy Difficulty:")
    for difficulty in ['easy', 'medium', 'hard']:
        if difficulty in results['by_difficulty']:
            stats = results['by_difficulty'][difficulty]
            print(f"  {difficulty}:")
            print(f"    Questions: {stats['total']}")
            print(f"    Success Rate: {stats['success_rate']:.1%}")
            print(f"    MRR: {stats['mrr']:.3f}")


def main():
    """Run example evaluation."""

    # Load evaluation suite
    eval_file = Path(__file__).parent / "search_eval_questions.json"
    eval_suite = load_evaluation_suite(eval_file)

    print(f"Loaded evaluation suite:")
    print(f"  Total questions: {eval_suite['metadata']['total_questions']}")
    print(f"  Repositories: {len(eval_suite['metadata']['repos'])}")

    # Run evaluation (with mock search function)
    print(f"\nRunning evaluation with mock search function...")
    print(f"(Replace mock_search_function with your actual implementation)")

    results = run_evaluation_example(eval_suite)

    # Print results
    print_results(results)

    print("\n" + "="*70)
    print("Note: This is a mock evaluation.")
    print("Replace mock_search_function with your actual search implementation.")
    print("="*70)


if __name__ == "__main__":
    main()
