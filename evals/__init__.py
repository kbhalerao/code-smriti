"""
V4 Evaluation Suite

Comprehensive evaluation framework for V4 ingestion results.

Modules:
- schema_coverage: Verify all V4 document types exist
- search_quality: Test vector search at symbol/file/module/repo levels
- rag_quality: Test end-to-end RAG answer quality

Usage:
    # Run all evaluations
    python -m evals.run_v4_eval --all

    # Run individual evaluations
    python -m evals.schema_coverage
    python -m evals.search_quality
    python -m evals.rag_quality
"""
