#!/usr/bin/env python3
"""
Validate that expected files in evaluation suite actually exist in repositories.
"""

import json
from pathlib import Path
from collections import defaultdict

REPOS_PATH = "/Users/kaustubh/Documents/codesmriti-repos"

REPO_DIR_MAP = {
    "kbhalerao/labcore": "kbhalerao_labcore",
    "JessiePBhalerao/firstseedtests": "JessiePBhalerao_firstseedtests",
    "kbhalerao/ask-kev-2026": "kbhalerao_ask-kev-2026",
    "kbhalerao/smartbarn2025": "kbhalerao_smartbarn2025",
    "kbhalerao/508hCoverCrop": "kbhalerao_508hCoverCrop"
}


def validate_evaluation_suite(json_file):
    """Validate that all expected files exist."""

    with open(json_file) as f:
        eval_suite = json.load(f)

    print("="*70)
    print("EVALUATION SUITE VALIDATION")
    print("="*70)
    print(f"\nValidating {len(eval_suite['questions'])} questions...")

    stats = {
        'total_questions': 0,
        'total_expected_files': 0,
        'files_found': 0,
        'files_not_found': 0,
        'partial_matches': 0,
        'questions_with_issues': []
    }

    for question in eval_suite['questions']:
        stats['total_questions'] += 1
        question_has_issue = False

        print(f"\n[Q{question['id']}] {question['query']}")

        for repo_id in question['expected_repos']:
            repo_dir = REPO_DIR_MAP.get(repo_id)

            if not repo_dir:
                print(f"  ! Unknown repo mapping: {repo_id}")
                question_has_issue = True
                continue

            repo_path = Path(REPOS_PATH) / repo_dir

            if not repo_path.exists():
                print(f"  ! Repo directory not found: {repo_path}")
                question_has_issue = True
                continue

            for expected_file in question['expected_files']:
                stats['total_expected_files'] += 1

                # Handle directory patterns (ending with /)
                if expected_file.endswith('/'):
                    # Check if directory exists
                    full_path = repo_path / expected_file
                    if full_path.exists() and full_path.is_dir():
                        print(f"  ✓ Directory exists: {expected_file}")
                        stats['files_found'] += 1
                    else:
                        print(f"  ✗ Directory not found: {expected_file}")
                        stats['files_not_found'] += 1
                        question_has_issue = True
                else:
                    # Check if file exists
                    full_path = repo_path / expected_file

                    if full_path.exists():
                        print(f"  ✓ File exists: {expected_file}")
                        stats['files_found'] += 1
                    else:
                        # Try to find partial matches
                        file_name = Path(expected_file).name
                        matches = list(repo_path.rglob(file_name))

                        if matches:
                            print(f"  ~ File found at different path: {expected_file}")
                            print(f"    Actual: {matches[0].relative_to(repo_path)}")
                            stats['partial_matches'] += 1
                        else:
                            print(f"  ✗ File not found: {expected_file}")
                            stats['files_not_found'] += 1
                            question_has_issue = True

        if question_has_issue:
            stats['questions_with_issues'].append(question['id'])

    # Summary
    print("\n" + "="*70)
    print("VALIDATION SUMMARY")
    print("="*70)
    print(f"\nQuestions: {stats['total_questions']}")
    print(f"Expected files/directories: {stats['total_expected_files']}")
    print(f"  Found: {stats['files_found']} ({stats['files_found']/stats['total_expected_files']*100:.1f}%)")
    print(f"  Partial matches: {stats['partial_matches']} ({stats['partial_matches']/stats['total_expected_files']*100:.1f}%)")
    print(f"  Not found: {stats['files_not_found']} ({stats['files_not_found']/stats['total_expected_files']*100:.1f}%)")

    if stats['questions_with_issues']:
        print(f"\nQuestions with issues: {len(stats['questions_with_issues'])}")
        print(f"  IDs: {stats['questions_with_issues']}")
    else:
        print(f"\n✓ All questions validated successfully!")

    return stats


if __name__ == "__main__":
    eval_file = Path(__file__).parent / "search_eval_questions.json"
    validate_evaluation_suite(eval_file)
