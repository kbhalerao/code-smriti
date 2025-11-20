# Code Search Quality Evaluation Suite

## Overview

This evaluation suite contains 37 carefully crafted questions designed to benchmark code search quality across 5 real-world repositories. The questions were created by analyzing actual code patterns, frameworks, and domain logic from production codebases.

## File Location

`/Users/kaustubh/Documents/code/code-smriti/4-consume/api-server/search_eval_questions.json`

## Repositories Sampled

1. **kbhalerao/labcore** (19 questions)
   - Django-based laboratory information management system
   - Features: Django Channels, Redis, GIS, PDF generation
   - Domain: Soil testing, agricultural data management

2. **kbhalerao/508hCoverCrop** (9 questions)
   - Agricultural field management with geospatial processing
   - Features: GRASS GIS, Django management commands, raster processing
   - Domain: Cover crop compliance, field boundaries

3. **kbhalerao/ask-kev-2026** (6 questions)
   - Modern chat interface built with Svelte 5
   - Features: Svelte runes ($state, $derived), Google authentication
   - Domain: Seed product information chatbot

4. **JessiePBhalerao/firstseedtests** (5 questions)
   - Seed variety testing and trial management
   - Features: Django, data processing, SVG visualization
   - Domain: Agricultural seed trials and reports

5. **kbhalerao/smartbarn2025** (0 questions)
   - Repository was empty during sampling

## Question Statistics

### By Category

| Category | Count | Description |
|----------|-------|-------------|
| framework_pattern | 12 | Framework-specific code patterns (Django, Svelte, Channels) |
| domain_logic | 10 | Business logic specific to agriculture, GIS, lab management |
| ui_component | 6 | Frontend components and UI patterns |
| architecture | 4 | Design patterns, error handling, decorators |
| testing | 2 | Test fixtures and test patterns |
| data_processing | 2 | Bulk data loading and transformations |
| api_usage | 1 | Library/API usage patterns |

### By Difficulty

| Difficulty | Count | Characteristics |
|------------|-------|-----------------|
| Medium | 21 | Requires understanding of framework concepts and domain |
| Easy | 9 | Straightforward framework usage or simple patterns |
| Hard | 7 | Complex patterns, multiple concepts, or specialized knowledge |

## Question Design Principles

1. **Realistic Queries**: Questions reflect what developers actually search for
   - "Django Channels background worker with job counter decorator"
   - "Svelte 5 component with runes for state management"
   - "GRASS GIS initialization and environment setup in Python"

2. **Diverse Coverage**:
   - Multiple programming languages (Python, JavaScript, TypeScript)
   - Various frameworks (Django, Svelte, Django Channels)
   - Different domains (agriculture, GIS, laboratory)
   - Cross-cutting concerns (testing, error handling, authentication)

3. **Specificity Levels**:
   - Specific API calls ("Django model aggregation with Avg and StdDev")
   - Framework patterns ("Django Channels SyncConsumer for background processing")
   - Domain concepts ("soil laboratory measurement data aggregation")
   - Architecture patterns ("async/sync wrapper pattern for decorator")

## Sample Questions

### Framework Pattern (Medium)
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

### Domain Logic (Hard)
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

### UI Component (Medium)
```json
{
  "id": 7,
  "query": "Svelte 5 component with runes for state management",
  "expected_repos": ["kbhalerao/ask-kev-2026"],
  "expected_files": ["src/lib/components/chat/ChatInput.svelte"],
  "category": "ui_component",
  "difficulty": "medium"
}
```

## Usage

### Running Evaluation

```python
import json

# Load evaluation suite
with open('search_eval_questions.json') as f:
    eval_suite = json.load(f)

# For each question
for question in eval_suite['questions']:
    query = question['query']
    expected_repos = question['expected_repos']
    expected_files = question['expected_files']

    # Run your search implementation
    results = your_search_function(query)

    # Evaluate results
    # - Check if expected repos are in top results
    # - Check if expected files are retrieved
    # - Calculate relevance metrics (precision, recall, MRR, etc.)
```

### Evaluation Metrics

Recommended metrics for benchmarking:

1. **Mean Reciprocal Rank (MRR)**: Rank of first relevant result
2. **Precision@K**: Proportion of relevant results in top K
3. **Recall@K**: Proportion of relevant results found in top K
4. **NDCG**: Normalized Discounted Cumulative Gain
5. **Success Rate**: Percentage of queries with at least 1 relevant result in top K

### Example Evaluation Code

```python
def evaluate_search_quality(search_function, eval_suite, k=10):
    """Evaluate search quality using the evaluation suite."""

    results = {
        'total_questions': len(eval_suite['questions']),
        'mrr_scores': [],
        'precision_at_k': [],
        'success_rate': 0
    }

    for question in eval_suite['questions']:
        # Run search
        search_results = search_function(question['query'], limit=k)

        # Extract file paths from results
        result_files = [r['file_path'] for r in search_results]

        # Calculate metrics
        relevant_found = any(
            any(exp_file in res_file for exp_file in question['expected_files'])
            for res_file in result_files
        )

        if relevant_found:
            results['success_rate'] += 1

            # Find rank of first relevant result
            for rank, res_file in enumerate(result_files, 1):
                if any(exp_file in res_file for exp_file in question['expected_files']):
                    results['mrr_scores'].append(1.0 / rank)
                    break
        else:
            results['mrr_scores'].append(0.0)

    # Calculate final metrics
    results['mrr'] = sum(results['mrr_scores']) / len(results['mrr_scores'])
    results['success_rate'] = results['success_rate'] / results['total_questions']

    return results
```

## Key Insights

### Technology Coverage

- **Frameworks**: Django (3.x+), Django Channels (3.x+), Svelte 5
- **Libraries**: GRASS GIS, WeasyPrint, Redis, PostgreSQL
- **Languages**: Python 3, TypeScript, JavaScript
- **Domains**: Agriculture, GIS, Laboratory Management, Chat Applications

### Challenging Questions

Some questions test sophisticated understanding:

1. **Async/Sync Wrapper Pattern** (Hard): Requires understanding both async and sync Python patterns
2. **GRASS GIS Setup** (Hard): Specialized geospatial library with complex initialization
3. **Split Billing Custom Fields** (Hard): Complex business logic with JSON data handling
4. **Raster Data Processing** (Hard): Specialized agricultural GIS processing

### Common Patterns Tested

- Decorator patterns (job counter, requeue, retry logic)
- Background task processing
- PDF generation workflows
- Geospatial data handling
- Modern frontend state management
- Django ORM advanced queries
- WebSocket consumers
- Data import/export scripts

## Future Enhancements

1. **Expand Coverage**: Add more repositories and domains
2. **Relevance Judgments**: Add graded relevance (0-3) instead of binary
3. **Negative Examples**: Include files that should NOT match
4. **Cross-Repo Questions**: Questions spanning multiple repositories
5. **Temporal Queries**: "Recent changes to authentication"
6. **Semantic Variations**: Multiple query phrasings for same intent

## Validation

All 37 questions were validated by:
1. Examining actual code files in the repositories
2. Verifying files exist at specified paths
3. Confirming queries match actual code patterns
4. Testing query diversity and specificity

## Notes on smartbarn2025

The `kbhalerao/smartbarn2025` repository contained no code files during sampling (empty repository). It was included in the target list but generated 0 questions.

## Created

2025-11-19

## Contact

For questions or suggestions about this evaluation suite, please refer to the main CodeSmriti project documentation.
