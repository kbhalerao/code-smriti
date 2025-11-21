#!/usr/bin/env python3
"""Analyze RAG response quality from evaluation results."""
import json
import sys

def analyze_quality(results_file):
    with open(results_file) as f:
        data = json.load(f)

    results = data['results']

    # Quality categories
    categories = {
        'json_only': [],           # Just returned JSON tool call
        'actual_code_snippets': [], # Has actual code from repos
        'repo_references': [],      # Mentions specific repos/files
        'generic_examples': [],     # Generic code examples
        'incomplete': []            # Didn't answer the question
    }

    repo_names = ['kbhalerao/', 'devanand', 'labcore', 'agkit', 'evolvechiro', '508h']

    for r in results:
        qid = r['question_id']
        answer = r['answer']
        query = r['query']

        # Check quality
        if answer.strip().startswith('{') or (answer.startswith('`{') and 'search_code' in answer[:50]):
            categories['json_only'].append((qid, query, answer[:100]))
        elif '```python' in answer or '```' in answer:
            # Has code blocks
            if any(repo in answer for repo in repo_names):
                categories['actual_code_snippets'].append((qid, query, answer[:200]))
            else:
                categories['generic_examples'].append((qid, query, answer[:200]))
        elif any(repo in answer for repo in repo_names):
            categories['repo_references'].append((qid, query, answer[:200]))
        else:
            categories['generic_examples'].append((qid, query, answer[:200]))

    # Print analysis
    print("="*80)
    print("RAG RESPONSE QUALITY ANALYSIS")
    print("="*80)
    print()

    total = len(results)

    for category, items in categories.items():
        count = len(items)
        pct = count / total * 100
        print(f"{category.replace('_', ' ').title()}: {count}/{total} ({pct:.1f}%)")

        if count > 0 and count <= 5:
            print("  Examples:")
            for qid, query, preview in items[:3]:
                print(f"    Q{qid}: {query[:50]}")
                print(f"         {preview.strip()[:80]}...")
        print()

    # Specific quality issues
    print("="*80)
    print("QUALITY ISSUES FOUND")
    print("="*80)

    # Find questions with expected files but got generic answers
    mismatches = []
    for r in results:
        expected_files = r.get('expected_files', [])
        answer = r['answer']
        if expected_files and not any(f in answer for f in expected_files):
            # Check if it at least has repo references
            if not any(repo in answer for repo in repo_names):
                mismatches.append((r['question_id'], r['query'], expected_files))

    if mismatches:
        print(f"\n{len(mismatches)} questions didn't find expected files:")
        for qid, query, expected in mismatches[:5]:
            print(f"  Q{qid}: {query[:60]}")
            print(f"       Expected: {expected}")
        print()

    # Success metrics
    print("="*80)
    print("SUCCESS METRICS")
    print("="*80)
    actual_code = len(categories['actual_code_snippets'])
    repo_refs = len(categories['repo_references'])
    json_only = len(categories['json_only'])

    useful_responses = actual_code + repo_refs
    print(f"Useful responses (has code or repo refs): {useful_responses}/{total} ({useful_responses/total*100:.1f}%)")
    print(f"Actual code from repos: {actual_code}/{total} ({actual_code/total*100:.1f}%)")
    print(f"Failed to use tools: {json_only}/{total} ({json_only/total*100:.1f}%)")
    print()

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python analyze_response_quality.py <eval_results.json>")
        sys.exit(1)

    analyze_quality(sys.argv[1])
