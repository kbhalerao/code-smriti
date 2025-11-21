#!/usr/bin/env python3
"""
Run RAG API evaluation with all 37 questions.
Monitors for crashes and saves intermediate results.
"""
import json
import time
import httpx
import asyncio
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime


async def run_evaluation(
    questions_file: str = "tests/search_eval_questions.json",
    api_url: str = "http://localhost:8000/api/chat/test",
    batch_size: int = 5,
    delay_between_batches: float = 2.0,
    timeout: float = 90.0,
    concurrent: bool = False
):
    """
    Run evaluation on all questions with batching and monitoring.

    Args:
        questions_file: Path to questions JSON
        api_url: RAG API endpoint
        batch_size: Number of questions per batch
        delay_between_batches: Seconds to wait between batches
        timeout: Request timeout in seconds
        concurrent: Run requests in parallel within each batch for throughput measurement
    """
    # Load questions
    with open(questions_file) as f:
        data = json.load(f)
        questions = data['questions']

    print(f"\n{'='*80}")
    print(f"RAG API Evaluation - {len(questions)} questions")
    print(f"Batch size: {batch_size}, Delay: {delay_between_batches}s")
    print(f"Mode: {'CONCURRENT (throughput test)' if concurrent else 'SEQUENTIAL (safe)'}")
    print(f"{'='*80}\n")

    results = []
    start_time = time.time()

    async with httpx.AsyncClient(timeout=timeout) as client:
        # Process in batches
        for batch_idx in range(0, len(questions), batch_size):
            batch = questions[batch_idx:batch_idx + batch_size]
            batch_num = batch_idx // batch_size + 1
            total_batches = (len(questions) + batch_size - 1) // batch_size

            print(f"\n[Batch {batch_num}/{total_batches}] Processing questions {batch_idx+1}-{batch_idx+len(batch)}...")

            batch_start_time = time.time()

            if concurrent:
                # Process batch concurrently for throughput measurement
                async def process_question(q):
                    q_id = q['id']
                    query = q['query']

                    try:
                        print(f"  Q{q_id}: Starting...", flush=True)

                        req_start = time.time()
                        response = await client.post(
                            api_url,
                            json={
                                "query": query,
                                "stream": False
                            }
                        )
                        req_time = time.time() - req_start

                        if response.status_code == 200:
                            data = response.json()
                            answer = data.get('answer', '')

                            result = {
                                'question_id': q_id,
                                'query': query,
                                'answer': answer,
                                'expected_repos': q.get('expected_repos', []),
                                'expected_files': q.get('expected_files', []),
                                'category': q.get('category', ''),
                                'difficulty': q.get('difficulty', ''),
                                'response_time': req_time,
                                'status': 'success',
                                'error': None
                            }

                            print(f"  Q{q_id}: ✓ ({req_time:.1f}s)", flush=True)
                        else:
                            result = {
                                'question_id': q_id,
                                'query': query,
                                'status': 'error',
                                'error': f"HTTP {response.status_code}: {response.text[:200]}",
                                'response_time': req_time
                            }
                            print(f"  Q{q_id}: ✗ HTTP {response.status_code}", flush=True)

                    except Exception as e:
                        result = {
                            'question_id': q_id,
                            'query': query,
                            'status': 'error',
                            'error': str(e),
                            'response_time': None
                        }
                        print(f"  Q{q_id}: ✗ Error: {str(e)[:50]}", flush=True)

                    return result

                # Run batch concurrently
                batch_results = await asyncio.gather(*[process_question(q) for q in batch])
                results.extend(batch_results)

            else:
                # Process batch sequentially (safer)
                for q in batch:
                    q_id = q['id']
                    query = q['query']

                    try:
                        print(f"  Q{q_id}: {query[:60]}...", end=" ", flush=True)

                        req_start = time.time()
                        response = await client.post(
                            api_url,
                            json={
                                "query": query,
                                "stream": False
                            }
                        )
                        req_time = time.time() - req_start

                        if response.status_code == 200:
                            data = response.json()
                            answer = data.get('answer', '')

                            result = {
                                'question_id': q_id,
                                'query': query,
                                'answer': answer,
                                'expected_repos': q.get('expected_repos', []),
                                'expected_files': q.get('expected_files', []),
                                'category': q.get('category', ''),
                                'difficulty': q.get('difficulty', ''),
                                'response_time': req_time,
                                'status': 'success',
                                'error': None
                            }

                            print(f"✓ ({req_time:.1f}s)")
                        else:
                            result = {
                                'question_id': q_id,
                                'query': query,
                                'status': 'error',
                                'error': f"HTTP {response.status_code}: {response.text[:200]}",
                                'response_time': req_time
                            }
                            print(f"✗ HTTP {response.status_code}")

                    except Exception as e:
                        result = {
                            'question_id': q_id,
                            'query': query,
                            'status': 'error',
                            'error': str(e),
                            'response_time': None
                        }
                        print(f"✗ Error: {str(e)[:50]}")

                    results.append(result)

            batch_time = time.time() - batch_start_time
            batch_throughput = len(batch) / batch_time if batch_time > 0 else 0

            print(f"  → Batch completed in {batch_time:.1f}s (throughput: {batch_throughput:.2f} q/s)")

            # Save intermediate results
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            intermediate_file = f"eval_results_batch{batch_num}_{timestamp}.json"
            with open(intermediate_file, 'w') as f:
                json.dump({
                    'batch': batch_num,
                    'total_batches': total_batches,
                    'results': results,
                    'timestamp': timestamp
                }, f, indent=2)

            print(f"  → Saved intermediate results to {intermediate_file}")

            # Delay between batches (except after last batch)
            if batch_idx + batch_size < len(questions):
                print(f"  → Waiting {delay_between_batches}s before next batch...")
                await asyncio.sleep(delay_between_batches)

    total_time = time.time() - start_time

    # Calculate summary statistics
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']

    avg_response_time = sum(r['response_time'] for r in successful) / len(successful) if successful else 0
    throughput = len(questions) / total_time if total_time > 0 else 0

    print(f"\n{'='*80}")
    print(f"Evaluation Complete!")
    print(f"{'='*80}")
    print(f"Total questions: {len(questions)}")
    print(f"Successful: {len(successful)} ({len(successful)/len(questions)*100:.1f}%)")
    print(f"Failed: {len(failed)} ({len(failed)/len(questions)*100:.1f}%)")
    print(f"Average response time: {avg_response_time:.2f}s")
    print(f"Total time: {total_time:.1f}s")
    print(f"Overall throughput: {throughput:.2f} questions/second")
    print(f"{'='*80}\n")

    # Save final results
    final_file = f"eval_results_final_{timestamp}.json"
    with open(final_file, 'w') as f:
        json.dump({
            'metadata': {
                'total_questions': len(questions),
                'successful': len(successful),
                'failed': len(failed),
                'avg_response_time': avg_response_time,
                'total_time': total_time,
                'throughput_qps': throughput,
                'concurrent_mode': concurrent,
                'batch_size': batch_size,
                'timestamp': timestamp
            },
            'results': results
        }, f, indent=2)

    print(f"✓ Final results saved to: {final_file}")

    # Print failed questions if any
    if failed:
        print(f"\n⚠ Failed questions ({len(failed)}):")
        for r in failed:
            print(f"  Q{r['question_id']}: {r['error']}")

    return results


if __name__ == "__main__":
    import sys

    # Allow command-line args
    batch_size = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    delay = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    concurrent = '--concurrent' in sys.argv or '-c' in sys.argv

    print(f"\nStarting evaluation:")
    print(f"  Batch size: {batch_size}")
    print(f"  Delay between batches: {delay}s")
    print(f"  Mode: {'CONCURRENT (throughput test)' if concurrent else 'SEQUENTIAL (safe)'}")
    print(f"\nUsage: python run_rag_eval.py [batch_size] [delay] [--concurrent]")
    print("Press Ctrl+C to abort\n")

    try:
        asyncio.run(run_evaluation(
            batch_size=batch_size,
            delay_between_batches=delay,
            concurrent=concurrent
        ))
    except KeyboardInterrupt:
        print("\n\n⚠ Evaluation aborted by user")
        sys.exit(1)
