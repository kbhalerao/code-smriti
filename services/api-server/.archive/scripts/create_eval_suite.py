#!/usr/bin/env python3
"""
Create evaluation suite by analyzing actual code from repositories.
"""

import json
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime

REPOS_PATH = "/Users/kaustubh/Documents/codesmriti-repos"

TARGET_REPOS = {
    "kbhalerao/labcore": "kbhalerao_labcore",
    "JessiePBhalerao/firstseedtests": "JessiePBhalerao_firstseedtests",
    "kbhalerao/ask-kev-2026": "kbhalerao_ask-kev-2026",
    "kbhalerao/smartbarn2025": "kbhalerao_smartbarn2025",
    "kbhalerao/508hCoverCrop": "kbhalerao_508hCoverCrop"
}

# File extensions to sample
CODE_EXTENSIONS = {
    '.py': 'python',
    '.js': 'javascript',
    '.jsx': 'javascript',
    '.ts': 'typescript',
    '.tsx': 'typescript',
    '.svelte': 'svelte',
    '.html': 'html',
    '.css': 'css',
    '.go': 'go',
    '.java': 'java',
    '.rb': 'ruby',
    '.rs': 'rust',
}

def sample_repo_files(repo_path, max_files=20):
    """Sample code files from a repository."""
    files_by_type = defaultdict(list)

    repo_path = Path(repo_path)

    for file_path in repo_path.rglob('*'):
        # Skip common directories
        if any(skip in file_path.parts for skip in [
            '.git', 'node_modules', 'venv', '__pycache__',
            'dist', 'build', '.next', 'coverage'
        ]):
            continue

        if file_path.is_file() and file_path.suffix in CODE_EXTENSIONS:
            lang = CODE_EXTENSIONS[file_path.suffix]
            rel_path = file_path.relative_to(repo_path)
            files_by_type[lang].append((str(rel_path), file_path))

    # Sample diverse files
    sampled_files = {}
    for lang, file_list in files_by_type.items():
        # Take first N files of each type
        for rel_path, full_path in file_list[:max_files]:
            sampled_files[rel_path] = {
                'language': lang,
                'path': str(full_path),
                'size': full_path.stat().st_size
            }

    return sampled_files, files_by_type


def analyze_repo_code(repo_id, repo_dir_name):
    """Analyze code to identify patterns for question generation."""
    repo_path = Path(REPOS_PATH) / repo_dir_name

    if not repo_path.exists():
        print(f"  ERROR: Repository path not found: {repo_path}")
        return None

    print(f"  Analyzing: {repo_path}")

    sampled_files, files_by_type = sample_repo_files(repo_path)

    patterns = {
        'frameworks': set(),
        'libraries': set(),
        'domains': set(),
        'file_types': {},
    }

    # Count files by type
    for lang, files in files_by_type.items():
        patterns['file_types'][lang] = len(files)

    # Analyze imports and patterns in Python files
    for file_path, info in list(sampled_files.items())[:30]:
        if info['language'] == 'python':
            try:
                with open(info['path'], 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(10000)  # First 10KB

                    # Detect frameworks
                    if 'django' in content.lower():
                        patterns['frameworks'].add('Django')
                    if 'channels' in content.lower():
                        patterns['frameworks'].add('Django Channels')
                    if 'fastapi' in content.lower():
                        patterns['frameworks'].add('FastAPI')
                    if 'flask' in content.lower():
                        patterns['frameworks'].add('Flask')
                    if 'celery' in content.lower():
                        patterns['libraries'].add('Celery')
                    if 'redis' in content.lower():
                        patterns['libraries'].add('Redis')
                    if 'postgres' in content.lower() or 'psycopg' in content.lower():
                        patterns['libraries'].add('PostgreSQL')

            except:
                pass

        elif info['language'] in ['javascript', 'typescript']:
            try:
                with open(info['path'], 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read(10000)

                    if 'react' in content.lower():
                        patterns['frameworks'].add('React')
                    if 'svelte' in content.lower():
                        patterns['frameworks'].add('Svelte')
                    if 'vue' in content.lower():
                        patterns['frameworks'].add('Vue')
                    if 'leaflet' in content.lower():
                        patterns['libraries'].add('Leaflet')
                    if 'mapbox' in content.lower():
                        patterns['libraries'].add('Mapbox')

            except:
                pass

    # Convert sets to lists for JSON
    patterns['frameworks'] = sorted(list(patterns['frameworks']))
    patterns['libraries'] = sorted(list(patterns['libraries']))
    patterns['domains'] = sorted(list(patterns['domains']))

    return {
        'sampled_files': sampled_files,
        'patterns': patterns,
        'stats': {
            'total_files': sum(len(files) for files in files_by_type.values()),
            'by_language': patterns['file_types']
        }
    }


def generate_questions_from_analysis(repo_id, analysis):
    """Generate evaluation questions based on repo analysis."""
    questions = []

    if not analysis:
        return questions

    patterns = analysis['patterns']
    sampled_files = analysis['sampled_files']

    # Generic file-based questions (for all repos)
    file_paths = list(sampled_files.keys())
    if file_paths:
        # Pick interesting files
        interesting_files = [f for f in file_paths if any(
            keyword in f.lower() for keyword in
            ['model', 'view', 'controller', 'service', 'util', 'helper',
             'component', 'consumer', 'task', 'api', 'test']
        )]

        for file_path in interesting_files[:3]:
            # Extract meaningful parts
            parts = Path(file_path).parts
            if len(parts) > 1:
                questions.append({
                    'query': f"{parts[-1].replace('_', ' ').replace('-', ' ')} in {parts[0] if parts[0] != parts[-1] else repo_id.split('/')[-1]}",
                    'expected_files': [file_path],
                    'category': 'architecture',
                    'difficulty': 'easy'
                })

    # Framework-specific questions
    if 'Django' in patterns['frameworks']:
        questions.extend([
            {
                'query': 'Django models definition',
                'expected_files': [f for f in file_paths if 'models.py' in f],
                'category': 'framework_pattern',
                'difficulty': 'easy'
            },
            {
                'query': 'Django views or API endpoints',
                'expected_files': [f for f in file_paths if 'views.py' in f or 'api.py' in f],
                'category': 'framework_pattern',
                'difficulty': 'easy'
            }
        ])

    if 'Django Channels' in patterns['frameworks']:
        questions.append({
            'query': 'Django Channels WebSocket consumer',
            'expected_files': [f for f in file_paths if 'consumer' in f.lower()],
            'category': 'framework_pattern',
            'difficulty': 'medium'
        })

    if 'React' in patterns['frameworks']:
        questions.extend([
            {
                'query': 'React component',
                'expected_files': [f for f in file_paths if f.endswith(('.jsx', '.tsx'))],
                'category': 'ui_component',
                'difficulty': 'easy'
            },
            {
                'query': 'React hooks usage',
                'expected_files': [f for f in file_paths if f.endswith(('.jsx', '.tsx'))],
                'category': 'api_usage',
                'difficulty': 'medium'
            }
        ])

    if 'Svelte' in patterns['frameworks']:
        questions.append({
            'query': 'Svelte component with stores',
            'expected_files': [f for f in file_paths if f.endswith('.svelte')],
            'category': 'ui_component',
            'difficulty': 'medium'
        })

    # Library-specific questions
    if 'Celery' in patterns['libraries']:
        questions.append({
            'query': 'Celery task definition',
            'expected_files': [f for f in file_paths if 'task' in f.lower() or 'celery' in f.lower()],
            'category': 'api_usage',
            'difficulty': 'medium'
        })

    if 'Leaflet' in patterns['libraries'] or 'Mapbox' in patterns['libraries']:
        questions.append({
            'query': 'Map component with layers',
            'expected_files': [f for f in file_paths if 'map' in f.lower()],
            'category': 'ui_component',
            'difficulty': 'medium'
        })

    # Testing patterns
    test_files = [f for f in file_paths if 'test' in f.lower()]
    if test_files:
        questions.append({
            'query': 'unit tests',
            'expected_files': test_files,
            'category': 'testing',
            'difficulty': 'easy'
        })

    # Add repo_id to all questions
    for q in questions:
        q['expected_repos'] = [repo_id]
        if not q['expected_files']:
            q['expected_files'] = []

    return questions


def main():
    print("\n" + "="*70)
    print("CREATING COMPREHENSIVE EVALUATION SUITE")
    print("="*70)

    all_questions = []
    repo_analyses = {}
    question_id = 1

    for repo_id, repo_dir_name in TARGET_REPOS.items():
        print(f"\n{'='*70}")
        print(f"Processing: {repo_id}")
        print('='*70)

        analysis = analyze_repo_code(repo_id, repo_dir_name)

        if analysis:
            repo_analyses[repo_id] = analysis

            print(f"\n  Found:")
            print(f"    Total files: {analysis['stats']['total_files']}")
            print(f"    Languages: {', '.join(analysis['stats']['by_language'].keys())}")
            print(f"    Frameworks: {', '.join(analysis['patterns']['frameworks']) or 'None detected'}")
            print(f"    Libraries: {', '.join(analysis['patterns']['libraries']) or 'None detected'}")

            # Generate questions
            questions = generate_questions_from_analysis(repo_id, analysis)

            # Add IDs
            for q in questions:
                q['id'] = question_id
                question_id += 1

            all_questions.extend(questions)
            print(f"    Generated {len(questions)} evaluation questions")
        else:
            print(f"  Skipped due to errors")

    # Create final evaluation suite
    eval_suite = {
        "metadata": {
            "created": datetime.now().strftime("%Y-%m-%d"),
            "total_questions": len(all_questions),
            "repos_sampled": len(repo_analyses),
            "repos": list(TARGET_REPOS.keys())
        },
        "questions": all_questions
    }

    # Save to file
    output_file = Path(__file__).parent / "search_eval_questions.json"
    with open(output_file, 'w') as f:
        json.dump(eval_suite, f, indent=2)

    print(f"\n{'='*70}")
    print(f"EVALUATION SUITE CREATED")
    print('='*70)
    print(f"\nOutput: {output_file}")
    print(f"Total questions: {len(all_questions)}")
    print(f"Repositories analyzed: {len(repo_analyses)}")

    # Summary by category
    by_category = defaultdict(int)
    by_difficulty = defaultdict(int)

    for q in all_questions:
        by_category[q['category']] += 1
        by_difficulty[q['difficulty']] += 1

    print(f"\nQuestions by category:")
    for cat, count in sorted(by_category.items()):
        print(f"  {cat}: {count}")

    print(f"\nQuestions by difficulty:")
    for diff, count in sorted(by_difficulty.items()):
        print(f"  {diff}: {count}")


if __name__ == "__main__":
    main()
