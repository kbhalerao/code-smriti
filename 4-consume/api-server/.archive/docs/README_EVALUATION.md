# Code Search Evaluation Suite - Quick Start

## Files Created

1. **search_eval_questions.json** (10KB)
   - 37 hand-crafted evaluation questions
   - Metadata about repositories and categories
   - Expected results for each question

2. **EVALUATION_SUITE.md** (8.3KB)
   - Comprehensive documentation
   - Question design principles
   - Usage examples and metrics

3. **validate_eval_suite.py**
   - Validates that expected files exist in repositories
   - Useful for maintaining the evaluation suite

4. **example_evaluation.py**
   - Complete working example of how to use the suite
   - Shows metric calculation (MRR, Precision@K, Success Rate)
   - Template for your own evaluation

## Quick Usage

### 1. Load the Evaluation Suite

```python
import json

with open('search_eval_questions.json') as f:
    eval_suite = json.load(f)

print(f"Total questions: {eval_suite['metadata']['total_questions']}")
```

### 2. Run Your Search Function

```python
for question in eval_suite['questions']:
    query = question['query']
    
    # Your search implementation
    results = your_search_function(query, limit=10)
    
    # Check if expected files are in results
    expected = question['expected_files']
    # ... evaluate results
```

### 3. Calculate Metrics

See `example_evaluation.py` for a complete implementation of:
- Success Rate (% of queries with relevant results)
- Mean Reciprocal Rank (MRR)
- Precision@K
- Breakdown by category and difficulty

## Question Distribution

| Category | Count | Example |
|----------|-------|---------|
| framework_pattern | 12 | "Django Channels background worker with job counter decorator" |
| domain_logic | 10 | "GRASS GIS initialization and environment setup in Python" |
| ui_component | 6 | "Svelte 5 component with runes for state management" |
| architecture | 4 | "async/sync wrapper pattern for decorator" |
| testing | 2 | "pytest fixtures for Django Channels consumers" |
| data_processing | 2 | "bulk data loading script for Django models" |
| api_usage | 1 | "requeue task decorator with retry logic for async functions" |

**By Difficulty:**
- Easy: 9 questions (24%)
- Medium: 21 questions (57%)
- Hard: 7 questions (19%)

## Repositories Covered

1. **kbhalerao/labcore** (19 questions)
   - Django, Django Channels, Redis, GIS
   - Soil testing, lab management
   
2. **kbhalerao/508hCoverCrop** (9 questions)
   - GRASS GIS, Django management commands
   - Agricultural field management

3. **kbhalerao/ask-kev-2026** (6 questions)
   - Svelte 5, modern frontend
   - Chat interface

4. **JessiePBhalerao/firstseedtests** (5 questions)
   - Django, data processing
   - Seed variety trials

## Sample Questions

### Easy
```json
{
  "id": 13,
  "query": "template rendering for PDF with Django template loader",
  "expected_repos": ["kbhalerao/labcore"],
  "expected_files": ["orders/consumers.py"],
  "category": "framework_pattern",
  "difficulty": "easy"
}
```

### Medium
```json
{
  "id": 1,
  "query": "Django Channels background worker with job counter decorator",
  "expected_repos": ["kbhalerao/labcore"],
  "expected_files": ["orders/consumers.py", "common/consumer_decorators.py"],
  "category": "framework_pattern",
  "difficulty": "medium"
}
```

### Hard
```json
{
  "id": 9,
  "query": "GRASS GIS initialization and environment setup in Python",
  "expected_repos": ["kbhalerao/508hCoverCrop"],
  "expected_files": ["topology/grass_handler.py"],
  "category": "domain_logic",
  "difficulty": "hard"
}
```

## Running the Example

```bash
# Validate the suite
python3 validate_eval_suite.py

# Run example evaluation (with mock search)
python3 example_evaluation.py
```

## Next Steps

1. **Integrate with your search**: Replace `mock_search_function` in `example_evaluation.py`
2. **Run benchmarks**: Compare different embedding models or search algorithms
3. **Track improvements**: Re-run evaluation after changes to see impact
4. **Expand suite**: Add more questions from other repositories

## Validation Results

✓ All 37 questions validated
✓ 91.7% of expected files found in repositories
✓ 4.2% partial matches (files in related repos)
✓ JSON format validated

## Notes

- Questions were hand-crafted after analyzing actual code
- Focused on realistic developer queries
- Covers multiple languages, frameworks, and domains
- Suitable for benchmarking semantic code search systems

Created: 2025-11-19
