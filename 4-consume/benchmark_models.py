#!/usr/bin/env python3
"""
Benchmark script to compare LLM models on code-smriti RAG tasks.

Tests models on specific work questions requiring code examples and narrative.
"""

import requests
import time
import json
from datetime import datetime
from typing import Dict, List
from dataclasses import dataclass, asdict


@dataclass
class BenchmarkResult:
    """Results from a single benchmark run"""
    model: str
    question_id: str
    question: str
    answer: str
    time_seconds: float
    answer_length: int
    timestamp: str
    error: str = None


# Test questions - specific to your work
TEST_QUESTIONS = [
    {
        "id": "q1_labcore_access_control",
        "question": "Explain the purpose and illustrate with code how to do access control in models and views in the kbhalerao/labcore repository. Please provide markdown formatted response with code examples and citations.",
        "eval_criteria": "Code examples, technical accuracy, citation quality",
        "type": "code_heavy"
    },
    {
        "id": "q2_farmworth_design_audit",
        "question": "Provide a summary of the design guidelines audit process for frontend development and how it relates to the workspaces concept in farmworth. Please provide a well-structured markdown response with citations.",
        "eval_criteria": "Narrative quality, comprehensiveness, structure",
        "type": "narrative_heavy"
    }
]


# Models to test (you'll swap these manually in the API config)
MODELS_TO_TEST = [
    "llama3.1:latest",         # Default - 4.9 GB
    "mixtral:8x7b",            # Fast MoE - 26 GB
    "gpt-oss-safeguard:120b"   # Large quality - 65 GB
]


class ModelBenchmark:
    """Benchmark LLM models via code-smriti chat endpoint"""

    def __init__(self, api_url="http://localhost:8000/api/chat/test"):
        self.api_url = api_url
        self.results: List[BenchmarkResult] = []

    def test_question(self, model_name: str, question_data: Dict) -> BenchmarkResult:
        """
        Test a question and return results.

        Args:
            model_name: Name of the model being tested (for labeling)
            question_data: Question dictionary with id, question, etc.
        """
        question = question_data["question"]
        q_id = question_data["id"]

        print(f"\n{'='*80}")
        print(f"🧪 Testing: {model_name}")
        print(f"📝 Question: {q_id}")
        print(f"   {question[:100]}...")
        print(f"{'='*80}")

        payload = {
            "query": question,
            "stream": False
        }

        try:
            start_time = time.time()
            response = requests.post(self.api_url, json=payload, timeout=300)
            elapsed = time.time() - start_time

            response.raise_for_status()
            data = response.json()
            answer = data.get("answer", "")

            result = BenchmarkResult(
                model=model_name,
                question_id=q_id,
                question=question,
                answer=answer,
                time_seconds=elapsed,
                answer_length=len(answer),
                timestamp=datetime.now().isoformat()
            )

            print(f"✓ Completed in {elapsed:.2f}s")
            print(f"  Answer length: {len(answer)} chars")
            print(f"  Preview: {answer[:150]}...")

            return result

        except Exception as e:
            print(f"✗ Error: {str(e)}")
            return BenchmarkResult(
                model=model_name,
                question_id=q_id,
                question=question,
                answer="",
                time_seconds=0,
                answer_length=0,
                timestamp=datetime.now().isoformat(),
                error=str(e)
            )

    def run_model_tests(self, model_name: str):
        """
        Run both test questions for a given model.

        This assumes you've already configured the API server with the model.
        """
        print(f"\n\n{'#'*80}")
        print(f"# TESTING MODEL: {model_name}")
        print(f"{'#'*80}")

        for question_data in TEST_QUESTIONS:
            result = self.test_question(model_name, question_data)
            self.results.append(result)

            # Brief pause between questions
            time.sleep(2)

    def print_results_table(self):
        """Print results in a comparison table"""
        print("\n\n" + "="*80)
        print("📊 BENCHMARK RESULTS")
        print("="*80)

        # Print by question
        for question_data in TEST_QUESTIONS:
            q_id = question_data["id"]

            print(f"\n\n{'─'*80}")
            print(f"Question: {q_id}")
            print(f"Type: {question_data['type']}")
            print(f"Criteria: {question_data['eval_criteria']}")
            print(f"{'─'*80}")
            print(f"\n{'Model':<30} {'Time (s)':<12} {'Length':<10} {'Tokens/s':<10}")
            print("─" * 80)

            question_results = [r for r in self.results if r.question_id == q_id]

            for result in question_results:
                if result.error:
                    print(f"{result.model:<30} ERROR: {result.error}")
                    continue

                # Estimate tokens (rough: ~4 chars per token)
                est_tokens = result.answer_length / 4
                tokens_per_sec = est_tokens / result.time_seconds if result.time_seconds > 0 else 0

                print(
                    f"{result.model:<30} "
                    f"{result.time_seconds:<12.2f} "
                    f"{result.answer_length:<10} "
                    f"{tokens_per_sec:<10.1f}"
                )

    def print_detailed_answers(self):
        """Print full answers for manual evaluation"""
        print("\n\n" + "="*80)
        print("📄 DETAILED ANSWERS FOR MANUAL EVALUATION")
        print("="*80)

        for question_data in TEST_QUESTIONS:
            q_id = question_data["id"]
            question_results = [r for r in self.results if r.question_id == q_id]

            print(f"\n\n{'#'*80}")
            print(f"# QUESTION: {q_id}")
            print(f"# {question_data['question']}")
            print(f"#")
            print(f"# Evaluation criteria: {question_data['eval_criteria']}")
            print(f"{'#'*80}")

            for result in question_results:
                print(f"\n\n{'-'*80}")
                print(f"MODEL: {result.model}")
                print(f"Time: {result.time_seconds:.2f}s | Length: {result.answer_length} chars")
                print(f"{'-'*80}")

                if result.error:
                    print(f"ERROR: {result.error}")
                else:
                    print(result.answer)

                print(f"\n{'-'*80}")

    def save_results(self, output_file="benchmark_results.json"):
        """Save all results to JSON for later analysis"""
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "test_questions": TEST_QUESTIONS,
            "models_tested": MODELS_TO_TEST,
            "results": [asdict(r) for r in self.results],
            "evaluation_instructions": {
                "correctness": "Rate 1-5: Technical accuracy and factual correctness",
                "usefulness": "Rate 1-5: Practical value and actionability of response",
                "tone": "Rate 1-5: Professional, clear, appropriate for audience"
            }
        }

        with open(output_file, "w") as f:
            json.dump(output_data, f, indent=2)

        print(f"\n\n💾 Full results saved to: {output_file}")

    def create_evaluation_template(self, output_file="evaluation_scores.json"):
        """Create a template for manual scoring"""
        evaluation_template = {
            "instructions": "Score each model's answer on a scale of 1-5 for each criterion",
            "criteria": {
                "correctness": "Technical accuracy and factual correctness",
                "usefulness": "Practical value and actionability",
                "tone": "Professional, clear, appropriate"
            },
            "scores": []
        }

        for result in self.results:
            if not result.error:
                evaluation_template["scores"].append({
                    "model": result.model,
                    "question_id": result.question_id,
                    "correctness": None,
                    "usefulness": None,
                    "tone": None,
                    "notes": ""
                })

        with open(output_file, "w") as f:
            json.dump(evaluation_template, f, indent=2)

        print(f"📋 Evaluation template created: {output_file}")


def main():
    """Main benchmark execution"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Benchmark LLM models on code-smriti RAG tasks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage workflow:
  1. Configure API server with first model (llama3.1:latest)
  2. Run: python benchmark_models.py --model llama3.1:latest
  3. Configure API server with second model (mixtral:8x7b)
  4. Run: python benchmark_models.py --model mixtral:8x7b --append
  5. Configure API server with third model (gpt-oss-safeguard:120b)
  6. Run: python benchmark_models.py --model gpt-oss-safeguard:120b --append
  7. Review results in benchmark_results.json
        """
    )

    parser.add_argument(
        "--model",
        required=True,
        help="Name of model currently configured in API (for labeling results)"
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:8000/api/chat/test",
        help="Chat API test endpoint URL"
    )
    parser.add_argument(
        "--output",
        default="benchmark_results.json",
        help="Output file for results"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to existing results file (for testing multiple models)"
    )
    parser.add_argument(
        "--show-answers",
        action="store_true",
        help="Print full answers for manual review"
    )

    args = parser.parse_args()

    benchmark = ModelBenchmark(api_url=args.api_url)

    # Load existing results if appending
    if args.append and os.path.exists(args.output):
        try:
            with open(args.output, "r") as f:
                existing_data = json.load(f)
                for result_dict in existing_data.get("results", []):
                    benchmark.results.append(BenchmarkResult(**result_dict))
            print(f"📂 Loaded {len(benchmark.results)} existing results")
        except Exception as e:
            print(f"⚠️  Could not load existing results: {e}")

    try:
        # Run tests for the current model
        benchmark.run_model_tests(args.model)

        # Print summary
        benchmark.print_results_table()

        # Optionally print full answers
        if args.show_answers:
            benchmark.print_detailed_answers()

        # Save results
        benchmark.save_results(args.output)
        benchmark.create_evaluation_template("evaluation_scores.json")

        print("\n✅ Benchmark completed!")
        print(f"\nNext steps:")
        print(f"  1. Review answers in {args.output}")
        print(f"  2. Fill out evaluation_scores.json with your ratings")
        print(f"  3. If testing more models, configure API and run with --append")

    except KeyboardInterrupt:
        print("\n\n⚠️  Benchmark interrupted by user")
        if benchmark.results:
            print("Saving partial results...")
            benchmark.save_results(args.output)


if __name__ == "__main__":
    import os
    main()
