# Akriti LoRA

Fine-tune a small language model to have "intuition" about the labcore/agkit codebase.

## What is Akriti?

Akriti (Sanskrit: "form brought forth through creation") represents the invariant patterns that persist across implementations - the essential understanding of what the codebase does and how it works.

## Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│                    Training Pipeline                        │
├─────────────────────────────────────────────────────────────┤
│  1. Generate QA pairs via exploration (80B model)           │
│  2. Prepare dataset for MLX training                        │
│  3. LoRA fine-tune Qwen3-4B with MLX                        │
│  4. Fuse adapter and test                                   │
└─────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
cd services/akriti-lora

# Create virtual environment
uv venv
source .venv/bin/activate
uv pip install -e .

# 1. Generate QA pairs (interactive)
uv run python scripts/generate_qa_pairs.py --interactive

# 2. Prepare dataset
uv run python scripts/prepare_dataset.py

# 3. Train LoRA adapter
uv run python scripts/train_lora.py --iters 500

# 4. Test the model
uv run python scripts/train_lora.py --fuse --test
```

## Scripts

### `generate_qa_pairs.py`

Uses Qwen 80B to explore the codebase and generate QA pairs.

**Interactive mode** (recommended for first run):
```bash
uv run python scripts/generate_qa_pairs.py --interactive
```

Commands:
- `/explore <repo>` - Explore a repository
- `/search <query>` - Search codebase
- `/ask <question>` - Ask about codebase
- `/qa` - Show generated QA pairs
- `/save` - Save QA pairs
- `/quit` - Exit

**Batch mode**:
```bash
uv run python scripts/generate_qa_pairs.py --rounds 10
```

### `prepare_dataset.py`

Converts QA pairs to MLX chat format and creates train/valid splits.

```bash
uv run python scripts/prepare_dataset.py --train-ratio 0.9
```

### `train_lora.py`

Fine-tunes a base model using LoRA with MLX.

```bash
# Basic training
uv run python scripts/train_lora.py

# With custom parameters
uv run python scripts/train_lora.py \
    --model mlx-community/Qwen3-4B-Instruct-4bit \
    --iters 1000 \
    --batch-size 4 \
    --lora-layers 16

# Train, fuse, and test
uv run python scripts/train_lora.py --fuse --test
```

## Configuration

### Target Repositories

Edit `generate_qa_pairs.py` to change the default repos:

```python
parser.add_argument("--repos", nargs="+", default=[
    "kbhalerao/labcore",
    "kbhalerao/agkit.io-backend",
    "kbhalerao/agkit.io-ui",
])
```

### Model Selection

Default: `mlx-community/Qwen3-4B-Instruct-4bit`

Other options:
- `mlx-community/Qwen2.5-Coder-7B-Instruct-4bit` - Better code understanding
- `ibm-granite/granite-4.0-h-tiny` - Most efficient inference (MLX support ongoing)

## Requirements

- Apple Silicon Mac (M1/M2/M3)
- Python 3.11+
- LM Studio running Qwen 80B (for QA generation)
- code-smriti RAG API running

## Data Format

### QA Pairs (`qa_pairs.jsonl`)
```json
{"instruction": "What does the workflows module do?", "input": "", "output": "The workflows module..."}
```

### MLX Training Format (`train.jsonl`)
```json
{"messages": [{"role": "user", "content": "What does the workflows module do?"}, {"role": "assistant", "content": "The workflows module..."}]}
```

## Future: Integration

Once trained, the Akriti model can be integrated into `ask_codebase`:

```
ask_codebase() → Akriti model (fast) → confidence check
     ↓ (if low confidence)
Full RAG pipeline with retrieved context
```

This provides fast answers for common questions while falling back to RAG for specific queries.
