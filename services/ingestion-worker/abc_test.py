#!/usr/bin/env python3
"""
Quick A/B test: Granite vs Qwen on real files
Run: python abc_test.py
"""

import asyncio
import json
import re
import time
import httpx

# Test files
TEST_FILES = {
    "python": {
        "path": "v4/aggregator.py",
        "language": "python"
    },
    "rst": {
        "path": "numpy_readme.rst",  # We'll pass content directly
        "language": "rst"
    }
}

MODELS = {
    "granite": {"id": "ibm/granite-4-h-tiny", "name": "Granite 4 Tiny"},
    "qwen": {"id": "qwen/qwen3-30b-a3b-2507", "name": "Qwen3 30B"},
    "minimax": {"id": "minimax/minimax-m2", "name": "MiniMax M2"},
}

BASE_URL = "http://macstudio.local:1234"

# Optimized prompt for smaller models:
# - Shorter, clearer instructions
# - Simpler JSON structure (flat, no nesting)
# - Explicit format example
# - "Think step by step" removed (wastes tokens on small models)
SUMMARY_PROMPT = """Summarize this {language} file as JSON.

File: {file_path}
```
{content}
```

Return ONLY this JSON (no markdown, no explanation):
{{
  "summary": "What this file does in 1-2 sentences",
  "purpose": "Why it exists",
  "key_elements": ["main", "classes", "or", "functions"],
  "quality_notes": "Issues found or null"
}}"""


async def call_model(model_id: str, prompt: str) -> tuple[str, float]:
    """Call LM Studio model."""
    async with httpx.AsyncClient(timeout=300.0) as client:  # 5min for minimax
        start = time.perf_counter()

        response = await client.post(
            f"{BASE_URL}/v1/responses",
            json={
                "model": model_id,
                "input": prompt,  # Prompt is self-contained now
                "temperature": 0.1,  # Lower temp for more consistent JSON
                "max_output_tokens": 1500
            }
        )
        response.raise_for_status()

        latency = (time.perf_counter() - start) * 1000

        data = response.json()
        output = data.get("output", [])
        for item in output:
            if item.get("type") == "message":
                content = item.get("content", [])
                for block in content:
                    if block.get("type") == "output_text":
                        return block.get("text", ""), latency

        return str(data), latency


def parse_json(response: str) -> tuple[bool, dict | None]:
    """Try to parse JSON from response."""
    json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
    if json_match:
        response = json_match.group(1)
    response = response.strip()

    try:
        return True, json.loads(response)
    except:
        try:
            fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', response)
            return True, json.loads(fixed)
        except:
            return False, None


async def test_file(file_type: str, content: str, file_path: str, language: str):
    """Test both models on a file."""
    print(f"\n{'='*70}")
    print(f"TEST: {file_type.upper()} - {file_path}")
    print(f"{'='*70}")

    # Truncate content for prompt
    content_truncated = content[:8000]

    prompt = SUMMARY_PROMPT.format(
        language=language,
        file_path=file_path,
        content=content_truncated
    )

    results = {}

    for model_key, model_info in MODELS.items():
        print(f"\n--- {model_info['name']} ---")

        try:
            raw, latency = await call_model(model_info["id"], prompt)
            valid, parsed = parse_json(raw)

            print(f"Latency: {latency:.0f}ms")
            print(f"JSON Valid: {'Yes' if valid else 'No'}")

            if valid and parsed:
                print(f"\nSummary: {parsed.get('summary', 'N/A')[:200]}")
                print(f"Key elements: {parsed.get('key_elements', [])[:5]}")
            else:
                print(f"Raw (first 300 chars): {raw[:300]}")

            results[model_key] = {
                "latency_ms": latency,
                "json_valid": valid,
                "parsed": parsed,
                "raw": raw
            }

        except Exception as e:
            print(f"ERROR: {e}")
            results[model_key] = {"error": str(e)}

    return results


async def main():
    # Load test files
    python_content = open("v4/aggregator.py").read()

    rst_content = """==================================
A guide to masked arrays in NumPy
==================================

History
-------

As a regular user of MaskedArray, I became increasingly frustrated with the
subclassing of masked arrays. I needed to develop a class of arrays that could
store some additional information along with numerical values, while keeping
the possibility for missing data.

Main differences
----------------

* The _data part of the masked array can be any subclass of ndarray
* fill_value is now a property, not a function
* the mask is forced to nomask when no value is actually masked
* put, putmask and take now mimic the ndarray methods

New features
------------

* the mr_ function mimics r_ for masked arrays
* the anom method returns the anomalies (deviations from the average)

Optimizing maskedarray
----------------------

Should masked arrays be filled before processing or not? The current
implementation involves filling arrays, performing operations, then
setting masks from input masks and domain masks.

A quick benchmark gives:
* numpy.ma.divide: 2.69 ms per loop
* division w/o filling: 1.55 ms per loop

So, is it worth filling the arrays beforehand? Yes for avoiding floating-point
exceptions. No if only interested in speed.
"""

    print("\n" + "="*70)
    print("A/B COMPARISON: Granite 4 Tiny vs Qwen3 30B")
    print("="*70)

    # Test Python
    py_results = await test_file(
        "python",
        python_content,
        "v4/aggregator.py",
        "python"
    )

    # Test RST
    rst_results = await test_file(
        "rst",
        rst_content,
        "numpy_masked_arrays.rst",
        "rst"
    )

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)

    for model_key, model_info in MODELS.items():
        py = py_results.get(model_key, {})
        rst = rst_results.get(model_key, {})

        py_ok = py.get("json_valid", False)
        rst_ok = rst.get("json_valid", False)
        py_lat = py.get("latency_ms", 0)
        rst_lat = rst.get("latency_ms", 0)

        print(f"\n{model_info['name']}:")
        print(f"  Python: {'PASS' if py_ok else 'FAIL'} ({py_lat:.0f}ms)")
        print(f"  RST:    {'PASS' if rst_ok else 'FAIL'} ({rst_lat:.0f}ms)")

    # Write full results for review
    with open("abc_results.json", "w") as f:
        json.dump({
            "python": py_results,
            "rst": rst_results
        }, f, indent=2, default=str)

    print("\nFull results saved to abc_results.json")


if __name__ == "__main__":
    asyncio.run(main())
