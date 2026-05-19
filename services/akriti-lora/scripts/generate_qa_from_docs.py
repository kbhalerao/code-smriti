#!/usr/bin/env python3
"""
Generate QA pairs by extracting from documentation files.

This is a mechanical, grounded approach:
1. Fetch each doc from smriti
2. Ask 80B to generate QA pairs from its content
3. Answers MUST come from the document - no hallucination

Usage:
    uv run python scripts/generate_qa_from_docs.py
    uv run python scripts/generate_qa_from_docs.py --repos kbhalerao/labcore
    uv run python scripts/generate_qa_from_docs.py --limit 10  # test with 10 docs
"""

import json
import os
import sys
import re
import time
from pathlib import Path
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

# LM Studio API endpoint
LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions"
MODEL = "qwen/qwen3-next-80b"

# Smriti API endpoints
API_BASE = os.getenv("CODESMRITI_API_URL", "http://macstudio.local") + "/api/rag"
API_USERNAME = os.getenv("CODESMRITI_USERNAME", "")
API_PASSWORD = os.getenv("CODESMRITI_PASSWORD", "")

# Token cache
_auth_token: str | None = None


class Colors:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    END = "\033[0m"


def get_auth_token() -> str:
    """Get JWT token for API authentication."""
    global _auth_token
    if _auth_token:
        return _auth_token

    if not API_USERNAME or not API_PASSWORD:
        raise ValueError("CODESMRITI_USERNAME and CODESMRITI_PASSWORD must be set in .env")

    base_url = os.getenv("CODESMRITI_API_URL", "http://macstudio.local")
    response = httpx.post(
        f"{base_url}/api/auth/login",
        json={"email": API_USERNAME, "password": API_PASSWORD},
        timeout=30.0,
        verify=False
    )
    response.raise_for_status()
    _auth_token = response.json()["token"]
    return _auth_token


def get_auth_headers() -> dict:
    """Get authorization headers for API calls."""
    token = get_auth_token()
    return {"Authorization": f"Bearer {token}"}


def list_doc_files(repo_id: str, path: str = "") -> list[dict]:
    """List documentation files in a directory using explore_structure API."""
    base_url = os.getenv("CODESMRITI_API_URL", "http://macstudio.local")

    response = httpx.post(
        f"{base_url}/api/rag/structure",
        json={
            "repo_id": repo_id,
            "path": path,
            "include_summaries": False,
        },
        headers=get_auth_headers(),
        timeout=60.0,
        verify=False
    )

    if response.status_code != 200:
        print(f"Explore API error: {response.status_code} - {response.text}")
        return []

    return response.json()


def get_all_doc_files(repo_id: str) -> list[dict]:
    """Recursively find all .md and .rst files in a repo."""
    all_docs = []
    dirs_to_explore = [""]  # Start at root
    explored = set()

    while dirs_to_explore:
        current_path = dirs_to_explore.pop(0)
        if current_path in explored:
            continue
        explored.add(current_path)

        result = list_doc_files(repo_id, current_path)
        if not result:
            continue

        # Add subdirectories to explore
        for subdir in result.get("directories", []):
            subdir_path = f"{current_path}/{subdir}".strip("/")
            # Skip certain directories
            skip_dirs = ["build", "node_modules", ".git", "staticfiles", "dist", "__pycache__"]
            if subdir not in skip_dirs:
                dirs_to_explore.append(subdir_path)

        # Collect doc files
        for f in result.get("files", []):
            file_path = f.get("path", "")
            if file_path.endswith((".md", ".rst", ".txt")):
                all_docs.append({
                    "file_path": file_path,
                    "repo_id": repo_id,
                    "language": f.get("language", ""),
                    "lines": f.get("lines", 0),
                })

    return all_docs


def search_docs(repo_filter: str, limit: int | None = None) -> list[dict]:
    """Get all documentation files in a repo by exploring the directory structure."""
    print(f"  Exploring {repo_filter} for documentation files...")

    all_docs = get_all_doc_files(repo_filter)

    # Filter out certain files
    skip_patterns = ["CHANGELOG", "LICENSE", "requirements", "setup.py", ".lock", "SOURCES.txt"]
    filtered_docs = []
    for doc in all_docs:
        path = doc.get("file_path", "")
        if not any(pattern.lower() in path.lower() for pattern in skip_patterns):
            filtered_docs.append(doc)

    # Respect limit if specified
    if limit and len(filtered_docs) > limit:
        filtered_docs = filtered_docs[:limit]

    return filtered_docs


def get_file_content(repo_id: str, file_path: str) -> str | None:
    """Fetch file content from smriti using file API."""
    base_url = os.getenv("CODESMRITI_API_URL", "http://macstudio.local")

    try:
        response = httpx.post(
            f"{base_url}/api/rag/file",
            json={
                "repo_id": repo_id,
                "file_path": file_path,
            },
            headers=get_auth_headers(),
            timeout=120.0,  # Increased timeout
            verify=False
        )
        if response.status_code != 200:
            print(f"      File API error: {response.status_code} - {response.text[:200]}")
            return None
        data = response.json()
        content = data.get("code", "")  # API returns "code" not "content"
        if not content:
            print(f"      Empty content returned for {file_path}")
        return content
    except httpx.TimeoutException:
        print(f"      Timeout fetching {file_path}")
    except Exception as e:
        print(f"      Error fetching {file_path}: {e}")

    return None


def call_llm(messages: list[dict], temperature: float = 0.7, max_tokens: int = 2048) -> str:
    """Call LM Studio API."""
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    response = httpx.post(LM_STUDIO_URL, json=payload, timeout=300.0)
    response.raise_for_status()

    result = response.json()
    return result["choices"][0]["message"]["content"]


# =============================================================================
# QA EXTRACTION
# =============================================================================

EXTRACTION_PROMPT = """You are extracting training data from documentation.

## Document
Path: {file_path}
Repository: {repo_id}

---
{document_content}
---

## Task

Generate 3-5 QA pairs from this document. Each pair should:

1. Be answerable ONLY from the content above - do not invent details
2. Be useful to one of these audiences:
   - **CEO/CTO**: Evaluating AgKit as a platform choice
   - **Engineer**: Building on or extending AgKit
   - **Agronomist**: Using AgKit for field operations

3. Be specific - reference concrete features, patterns, or capabilities mentioned
4. Sound natural - phrase questions as a real person would ask them

## Format

Output each pair as:
Q: [question]
A: [answer using specifics from the document]

Generate pairs now:"""


def extract_qa_from_doc(repo_id: str, file_path: str, content: str) -> list[dict]:
    """Extract QA pairs from a single document."""

    # Skip if content is too short
    if len(content) < 200:
        return []

    prompt = EXTRACTION_PROMPT.format(
        file_path=file_path,
        repo_id=repo_id,
        document_content=content[:8000],  # Truncate very long docs
    )

    messages = [{"role": "user", "content": prompt}]

    try:
        response = call_llm(messages, temperature=0.5, max_tokens=1500)
        return parse_qa_pairs(response, repo_id, file_path)
    except Exception as e:
        print(f"      LLM error: {e}")
        return []


def parse_qa_pairs(response: str, repo_id: str, file_path: str) -> list[dict]:
    """Parse QA pairs from LLM response."""
    pairs = []

    # Pattern: Q: ... A: ...
    qa_pattern = r'Q:\s*(.+?)\s*\n+A:\s*(.+?)(?=\n\nQ:|\n\n[A-Z]|\Z)'
    for match in re.finditer(qa_pattern, response, re.DOTALL | re.IGNORECASE):
        q = match.group(1).strip()
        a = match.group(2).strip()

        # Clean up
        q = re.sub(r'\*\*', '', q)  # Remove bold markers
        a = re.sub(r'\*\*', '', a)

        if q and a and len(a) > 30:
            pairs.append({
                "instruction": q,
                "output": a,
                "input": "",
                "source_repo": repo_id,
                "source_file": file_path,
            })

    return pairs


def append_qa_pairs(pairs: list[dict], filename: str = "qa_pairs_v3.jsonl"):
    """Append QA pairs to output file."""
    output_file = Path(__file__).parent.parent / "data" / filename
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "a") as f:
        for qa in pairs:
            f.write(json.dumps(qa) + "\n")


def load_processed_files(filename: str = "qa_pairs_v3.jsonl") -> set[str]:
    """Load set of already-processed files to enable resume."""
    output_file = Path(__file__).parent.parent / "data" / filename
    processed = set()
    if output_file.exists():
        with open(output_file) as f:
            for line in f:
                try:
                    qa = json.loads(line)
                    key = f"{qa.get('source_repo', '')}:{qa.get('source_file', '')}"
                    processed.add(key)
                except:
                    pass
    return processed


# =============================================================================
# MAIN
# =============================================================================

def process_repo(repo_id: str, limit: int | None = None, skip_processed: bool = True) -> int:
    """Process all docs in a repo."""
    print(f"\n{Colors.CYAN}Processing repo: {repo_id}{Colors.END}")

    # Get list of docs
    docs = search_docs(repo_id, limit=limit)
    print(f"  Found {len(docs)} documents")

    # Load already-processed files
    processed = load_processed_files() if skip_processed else set()
    print(f"  Already processed: {len(processed)} files")

    total_pairs = 0
    for i, doc in enumerate(docs):
        file_path = doc.get("file_path") or doc.get("path", "unknown")
        content = doc.get("content", "")

        # Skip if already processed
        key = f"{repo_id}:{file_path}"
        if key in processed:
            continue

        # Skip non-documentation files
        if not any(file_path.endswith(ext) for ext in [".md", ".rst", ".txt"]):
            continue

        # Skip certain files
        skip_patterns = ["CHANGELOG", "LICENSE", "requirements", "setup.py", ".lock"]
        if any(pattern.lower() in file_path.lower() for pattern in skip_patterns):
            continue

        print(f"\n  [{i+1}/{len(docs)}] {file_path}")

        # If content not in search result, try to fetch it
        if not content or len(content) < 100:
            content = get_file_content(repo_id, file_path)
            if not content:
                print(f"      Skipped (no content)")
                continue

        # Extract QA pairs
        pairs = extract_qa_from_doc(repo_id, file_path, content)

        if pairs:
            append_qa_pairs(pairs)
            total_pairs += len(pairs)
            print(f"      {Colors.GREEN}Generated {len(pairs)} QA pairs{Colors.END}")
            for qa in pairs:
                print(f"        Q: {qa['instruction'][:60]}...")
        else:
            print(f"      {Colors.YELLOW}No pairs extracted{Colors.END}")

        # Rate limiting
        time.sleep(0.5)

        # Limit for testing
        if limit and i >= limit - 1:
            break

    return total_pairs


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate QA pairs from documentation files"
    )
    parser.add_argument("--repos", nargs="+", default=[
        "kbhalerao/labcore",
        "kbhalerao/agkit.io-backend",
    ], help="Repositories to process")
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit docs per repo (for testing)")
    parser.add_argument("--no-skip", action="store_true",
                        help="Don't skip already-processed files")
    args = parser.parse_args()

    # Check connectivity
    print("Checking API connectivity...")
    try:
        get_auth_token()
        print(f"  RAG API: OK")
    except Exception as e:
        print(f"  RAG API error: {e}")
        sys.exit(1)

    try:
        httpx.post(
            LM_STUDIO_URL,
            json={"model": MODEL, "messages": [{"role": "user", "content": "test"}], "max_tokens": 10},
            timeout=30.0
        )
        print(f"  LM Studio: OK")
    except Exception as e:
        print(f"  LM Studio error: {e}")
        sys.exit(1)

    # Process repos
    total = 0
    for repo in args.repos:
        count = process_repo(repo, limit=args.limit, skip_processed=not args.no_skip)
        total += count

    print(f"\n{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.GREEN}Total QA pairs generated: {total}{Colors.END}")
    print(f"Output: data/qa_pairs_v3.jsonl")


if __name__ == "__main__":
    main()
