#!/usr/bin/env python3
"""
V4 Evaluation Suite Runner

Orchestrates all V4 evaluations:
1. Schema Coverage - Verify all document types exist
2. Search Quality - Test vector search at all levels
3. RAG Quality - Test end-to-end answer quality

Usage:
    # Run all evaluations
    python run_v4_eval.py --all

    # Run specific evaluations
    python run_v4_eval.py --schema --search
    python run_v4_eval.py --rag

    # Use custom questions file
    python run_v4_eval.py --all --questions v4_eval_questions.json
"""
import asyncio
import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))


async def run_schema_eval(output_dir: Path) -> dict:
    """Run schema coverage evaluation."""
    print("\n" + "=" * 70)
    print("RUNNING SCHEMA COVERAGE EVALUATION")
    print("=" * 70)

    from evals.schema_coverage import SchemaCoverageEvaluator

    evaluator = SchemaCoverageEvaluator()
    results = await evaluator.run_all()

    output_file = output_dir / f"schema_coverage_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"Schema results saved to: {output_file}")

    return results


async def run_search_eval(questions_file: str, output_dir: Path) -> dict:
    """Run search quality evaluation."""
    print("\n" + "=" * 70)
    print("RUNNING SEARCH QUALITY EVALUATION")
    print("=" * 70)

    from evals.search_quality import SearchQualityEvaluator, load_eval_questions

    # Load questions (filter to search-appropriate ones)
    all_questions = load_eval_questions(questions_file)
    search_questions = [q for q in all_questions if not q.get("rag_only", False)]

    evaluator = SearchQualityEvaluator()
    results = await evaluator.run_eval_suite(search_questions)

    output_file = output_dir / f"search_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"Search results saved to: {output_file}")

    return results


async def run_rag_eval(questions_file: str, output_dir: Path) -> dict:
    """Run RAG quality evaluation."""
    print("\n" + "=" * 70)
    print("RUNNING RAG QUALITY EVALUATION")
    print("=" * 70)

    from evals.rag_quality import RAGQualityEvaluator, load_rag_questions

    # Load questions (filter to RAG-appropriate ones)
    all_questions = load_rag_questions(questions_file)
    rag_questions = [q for q in all_questions if q.get("rag_only", True) or q.get("category") in ["code_explanation", "architecture", "discovery", "implementation"]]

    evaluator = RAGQualityEvaluator()
    results = await evaluator.run_eval_suite(rag_questions)

    output_file = output_dir / f"rag_quality_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"RAG results saved to: {output_file}")

    return results


async def main():
    parser = argparse.ArgumentParser(
        description="V4 Evaluation Suite Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run all evaluations
    python run_v4_eval.py --all

    # Run only schema and search
    python run_v4_eval.py --schema --search

    # Run with custom questions
    python run_v4_eval.py --all --questions my_questions.json

    # Specify output directory
    python run_v4_eval.py --all --output-dir /tmp/evals
        """
    )

    parser.add_argument("--all", action="store_true", help="Run all evaluations")
    parser.add_argument("--schema", action="store_true", help="Run schema coverage evaluation")
    parser.add_argument("--search", action="store_true", help="Run search quality evaluation")
    parser.add_argument("--rag", action="store_true", help="Run RAG quality evaluation")
    parser.add_argument("--questions", default=str(Path(__file__).parent / "v4_eval_questions.json"), help="Questions JSON file")
    parser.add_argument("--output-dir", default=str(Path(__file__).parent / "results"), help="Output directory for results")

    args = parser.parse_args()

    # Default to --all if no specific eval selected
    if not (args.schema or args.search or args.rag):
        args.all = True

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Track results
    all_results = {
        "timestamp": datetime.now().isoformat(),
        "questions_file": args.questions,
        "evaluations": {}
    }

    print("=" * 70)
    print("V4 EVALUATION SUITE")
    print("=" * 70)
    print(f"Questions file: {args.questions}")
    print(f"Output directory: {output_dir}")
    print(f"Running: ", end="")
    if args.all:
        print("ALL (schema, search, rag)")
    else:
        parts = []
        if args.schema: parts.append("schema")
        if args.search: parts.append("search")
        if args.rag: parts.append("rag")
        print(", ".join(parts))
    print("=" * 70)

    start_time = datetime.now()

    # Run evaluations
    if args.all or args.schema:
        try:
            schema_results = await run_schema_eval(output_dir)
            all_results["evaluations"]["schema"] = {
                "status": "completed",
                "pass": schema_results.get("overall_pass", False),
                "summary": {
                    "repos_coverage": schema_results["evaluations"]["repo_coverage"]["coverage_pct"],
                    "total_files": schema_results["evaluations"]["file_coverage"]["total_files"],
                    "total_symbols": schema_results["evaluations"]["symbol_coverage"]["total_symbols"],
                    "unknown_symbols": schema_results["evaluations"]["symbol_coverage"]["unknown_symbol_names"]
                }
            }
        except Exception as e:
            print(f"\nSchema evaluation failed: {e}")
            all_results["evaluations"]["schema"] = {"status": "failed", "error": str(e)}

    if args.all or args.search:
        try:
            search_results = await run_search_eval(args.questions, output_dir)
            all_results["evaluations"]["search"] = {
                "status": "completed",
                "summary": search_results.get("summary", {})
            }
        except Exception as e:
            print(f"\nSearch evaluation failed: {e}")
            all_results["evaluations"]["search"] = {"status": "failed", "error": str(e)}

    if args.all or args.rag:
        try:
            rag_results = await run_rag_eval(args.questions, output_dir)
            all_results["evaluations"]["rag"] = {
                "status": "completed",
                "summary": rag_results.get("summary", {})
            }
        except Exception as e:
            print(f"\nRAG evaluation failed: {e}")
            all_results["evaluations"]["rag"] = {"status": "failed", "error": str(e)}

    # Calculate total duration
    end_time = datetime.now()
    all_results["duration_seconds"] = (end_time - start_time).total_seconds()

    # Save combined results
    combined_file = output_dir / f"v4_eval_combined_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(combined_file, 'w') as f:
        json.dump(all_results, f, indent=2)

    # Print final summary
    print("\n" + "=" * 70)
    print("EVALUATION COMPLETE")
    print("=" * 70)
    print(f"Duration: {all_results['duration_seconds']:.1f}s")
    print(f"Combined results: {combined_file}")

    for eval_name, eval_result in all_results["evaluations"].items():
        status = eval_result.get("status", "unknown")
        if status == "completed":
            print(f"\n{eval_name.upper()}:")
            if eval_name == "schema":
                print(f"  Pass: {eval_result.get('pass', 'N/A')}")
                print(f"  Files: {eval_result['summary'].get('total_files', 'N/A')}")
                print(f"  Symbols: {eval_result['summary'].get('total_symbols', 'N/A')}")
            elif eval_name == "search":
                for level, stats in eval_result.get("summary", {}).items():
                    if isinstance(stats, dict):
                        print(f"  {level}: Hit={stats.get('hit_rate', 0)*100:.0f}% P@3={stats.get('avg_precision_at_3', 0):.2f}")
            elif eval_name == "rag":
                summary = eval_result.get("summary", {})
                print(f"  Quality Score: {summary.get('avg_quality_score', 0):.3f}")
                print(f"  Avg Response: {summary.get('avg_response_time', 0):.1f}s")
        else:
            print(f"\n{eval_name.upper()}: {status}")
            if "error" in eval_result:
                print(f"  Error: {eval_result['error'][:100]}")

    print("\n" + "=" * 70)

    return all_results


if __name__ == "__main__":
    asyncio.run(main())
