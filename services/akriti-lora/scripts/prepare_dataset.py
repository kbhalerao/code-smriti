#!/usr/bin/env python3
"""
Prepare QA pairs for MLX LoRA training.

Stratified split by:
- source_repo (ensure all repos represented in train/valid)
- source_file directory (ensure coverage across different doc areas)

Output format for MLX:
{"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}

Usage:
    uv run python scripts/prepare_dataset.py --input data/qa_pairs_v3.jsonl
"""

import json
import random
from pathlib import Path
from collections import defaultdict

# Reproducibility
SEED = 42


def load_qa_pairs(input_file: Path) -> list[dict]:
    """Load QA pairs from JSONL."""
    pairs = []
    with open(input_file) as f:
        for line in f:
            if line.strip():
                pairs.append(json.loads(line))
    return pairs


def get_stratification_key(qa: dict) -> str:
    """
    Create stratification key from repo and doc category.

    Groups files by:
    - repo
    - top-level directory (docs/, src/, root-level, etc.)
    """
    repo = qa.get("source_repo", "unknown")
    file_path = qa.get("source_file", "")

    # Extract top-level directory or "root"
    parts = file_path.split("/")
    if len(parts) > 1:
        top_dir = parts[0]
    else:
        top_dir = "root"

    return f"{repo}::{top_dir}"


def convert_to_chat_format(qa_pair: dict) -> dict:
    """
    Convert QA pair to MLX chat format.

    MLX expects: {"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}
    """
    instruction = qa_pair.get("instruction", "")
    input_text = qa_pair.get("input", "")
    output = qa_pair.get("output", "")

    # Combine instruction and input if input exists
    user_content = instruction
    if input_text:
        user_content = f"{instruction}\n\n{input_text}"

    return {
        "messages": [
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": output}
        ],
        # Preserve metadata for debugging
        "_source_repo": qa_pair.get("source_repo", ""),
        "_source_file": qa_pair.get("source_file", ""),
    }


def stratified_split(
    pairs: list[dict],
    valid_ratio: float = 0.1,
    min_valid_per_stratum: int = 1,
    seed: int = SEED
) -> tuple[list[dict], list[dict]]:
    """
    Split data with stratification by repo and doc category.

    Ensures each stratum has at least min_valid_per_stratum in validation.
    Falls back to train-only for very small strata.
    """
    random.seed(seed)

    # Group by stratification key
    strata = defaultdict(list)
    for qa in pairs:
        key = get_stratification_key(qa)
        strata[key].append(qa)

    train = []
    valid = []

    print(f"\nStratification breakdown:")
    print(f"{'Stratum':<50} {'Total':>8} {'Train':>8} {'Valid':>8}")
    print("-" * 78)

    for key, items in sorted(strata.items()):
        # Shuffle within stratum
        random.shuffle(items)

        # Calculate split
        n_valid = max(min_valid_per_stratum, int(len(items) * valid_ratio))
        n_valid = min(n_valid, len(items) - 1)  # Keep at least 1 for train

        if len(items) <= 2:
            # Very small stratum - put all in train
            stratum_train = items
            stratum_valid = []
        else:
            stratum_valid = items[:n_valid]
            stratum_train = items[n_valid:]

        train.extend(stratum_train)
        valid.extend(stratum_valid)

        print(f"{key:<50} {len(items):>8} {len(stratum_train):>8} {len(stratum_valid):>8}")

    print("-" * 78)
    print(f"{'TOTAL':<50} {len(pairs):>8} {len(train):>8} {len(valid):>8}")

    # Final shuffle to mix strata
    random.shuffle(train)
    random.shuffle(valid)

    return train, valid


def main():
    """Prepare dataset for MLX training."""
    import argparse

    parser = argparse.ArgumentParser(description="Prepare dataset for MLX LoRA training")
    parser.add_argument("--input", type=str, default=None, help="Input JSONL file")
    parser.add_argument("--output-dir", type=str, default=None, help="Output directory")
    parser.add_argument("--valid-ratio", type=float, default=0.1, help="Validation split ratio")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for split")
    args = parser.parse_args()

    data_dir = Path(__file__).parent.parent / "data"
    input_file = Path(args.input) if args.input else data_dir / "qa_pairs_v3.jsonl"
    output_dir = Path(args.output_dir) if args.output_dir else data_dir

    if not input_file.exists():
        print(f"Error: {input_file} not found. Run generate_qa_from_docs.py first.")
        return

    print(f"Loading QA pairs from {input_file}...")
    qa_pairs = load_qa_pairs(input_file)
    print(f"Loaded {len(qa_pairs)} QA pairs")

    # Check for duplicates
    seen = set()
    unique_pairs = []
    duplicates = 0
    for qa in qa_pairs:
        key = (qa.get("instruction", ""), qa.get("output", ""))
        if key not in seen:
            seen.add(key)
            unique_pairs.append(qa)
        else:
            duplicates += 1

    if duplicates > 0:
        print(f"Removed {duplicates} duplicate pairs")
        qa_pairs = unique_pairs

    # Filter out empty responses
    qa_pairs = [qa for qa in qa_pairs if qa.get("output", "").strip()]
    print(f"After filtering empty responses: {len(qa_pairs)} examples")

    # Stratified split
    print(f"\nPerforming stratified split (valid ratio: {args.valid_ratio})...")
    train_pairs, valid_pairs = stratified_split(
        qa_pairs,
        valid_ratio=args.valid_ratio,
        seed=args.seed
    )

    # Convert to chat format
    print("\nConverting to MLX chat format...")
    train_data = [convert_to_chat_format(qa) for qa in train_pairs]
    valid_data = [convert_to_chat_format(qa) for qa in valid_pairs]

    # Save datasets (strip metadata for training files)
    train_file = output_dir / "train.jsonl"
    valid_file = output_dir / "valid.jsonl"

    print(f"\nSaving to {output_dir}...")

    def strip_metadata(item):
        """Remove debug metadata for training."""
        return {"messages": item["messages"]}

    with open(train_file, "w") as f:
        for item in train_data:
            f.write(json.dumps(strip_metadata(item)) + "\n")

    with open(valid_file, "w") as f:
        for item in valid_data:
            f.write(json.dumps(strip_metadata(item)) + "\n")

    print(f"\nDataset prepared:")
    print(f"  Train: {train_file} ({len(train_data)} examples)")
    print(f"  Valid: {valid_file} ({len(valid_data)} examples)")

    # Print samples
    print("\nSample training example:")
    sample = train_data[0] if train_data else None
    if sample:
        print(f"  User: {sample['messages'][0]['content'][:100]}...")
        print(f"  Assistant: {sample['messages'][1]['content'][:100]}...")

    # Verify stratification
    print("\nValidation set repo coverage:")
    valid_repos = set(d["_source_repo"] for d in valid_data)
    for repo in valid_repos:
        count = sum(1 for d in valid_data if d["_source_repo"] == repo)
        print(f"  {repo}: {count} examples")


if __name__ == "__main__":
    main()
