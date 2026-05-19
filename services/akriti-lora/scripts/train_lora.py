#!/usr/bin/env python3
"""
Train LoRA adapter using MLX.

This script fine-tunes a base model (Qwen3-4B) on the generated QA pairs
to create an Akriti-aware model.

Usage:
    uv run python scripts/train_lora.py
    uv run python scripts/train_lora.py --model mlx-community/Qwen3-4B-Instruct-4bit --iters 500
"""

import subprocess
import sys
from pathlib import Path


def check_mlx_lm():
    """Check if mlx-lm is installed."""
    try:
        import mlx_lm
        print(f"mlx-lm version: {mlx_lm.__version__}")
        return True
    except ImportError:
        print("mlx-lm not installed. Install with: pip install mlx-lm")
        return False


def download_model(model_name: str, quantize: bool = True):
    """Download and optionally quantize a model from HuggingFace."""
    print(f"\nDownloading model: {model_name}")

    cmd = ["mlx_lm.convert", "--hf-path", model_name]
    if quantize:
        cmd.append("-q")  # 4-bit quantization

    subprocess.run(cmd, check=True)


def create_lora_config(output_dir: Path, lora_rank: int = 8, lora_dropout: float = 0.05) -> Path:
    """Create a YAML config file for LoRA parameters."""
    import yaml

    config = {
        "lora_parameters": {
            "rank": lora_rank,
            "dropout": lora_dropout,
            "scale": 1.0,  # alpha/rank, keeping at 1.0 means alpha=rank
        }
    }

    config_path = output_dir / "lora_config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)

    return config_path


def train_lora(
    model: str,
    data_dir: Path,
    output_dir: Path,
    iters: int = 500,
    batch_size: int = 4,
    num_layers: int = 8,
    lora_rank: int = 8,
    lora_dropout: float = 0.05,
    learning_rate: float = 2e-5,
):
    """Run LoRA fine-tuning with MLX.

    Conservative defaults for ~1500 example dataset:
    - Low rank (8) to prevent overfitting
    - Low learning rate (2e-5) for stable convergence
    - Dropout (0.05) for regularization
    - Fewer layers (8) to limit capacity
    """
    print(f"\n{'='*60}")
    print("Starting LoRA Training")
    print(f"{'='*60}")
    print(f"Model: {model}")
    print(f"Data: {data_dir}")
    print(f"Output: {output_dir}")
    print(f"Iterations: {iters}")
    print(f"Batch size: {batch_size}")
    print(f"Num layers: {num_layers}")
    print(f"LoRA rank: {lora_rank}")
    print(f"LoRA dropout: {lora_dropout}")
    print(f"Learning rate: {learning_rate}")
    print(f"{'='*60}\n")

    # Create config file for LoRA parameters
    output_dir.mkdir(parents=True, exist_ok=True)
    config_path = create_lora_config(output_dir, lora_rank, lora_dropout)
    print(f"Created LoRA config: {config_path}")

    # Build mlx_lm.lora command
    cmd = [
        "mlx_lm.lora",
        "--model", model,
        "--train",
        "--data", str(data_dir),
        "--iters", str(iters),
        "--batch-size", str(batch_size),
        "--num-layers", str(num_layers),
        "--learning-rate", str(learning_rate),
        "--adapter-path", str(output_dir / "adapters"),
        "--config", str(config_path),
        "--steps-per-report", "10",
        "--steps-per-eval", "50",
    ]

    # Check for validation data
    if (data_dir / "valid.jsonl").exists():
        cmd.extend(["--val-batches", "20"])

    print(f"Command: {' '.join(cmd)}\n")

    # Run training
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"\nTraining failed with return code {result.returncode}")
        return False

    print(f"\n{'='*60}")
    print("Training Complete!")
    print(f"Adapters saved to: {output_dir / 'adapters'}")
    print(f"{'='*60}")

    return True


def fuse_adapter(
    model: str,
    adapter_path: Path,
    output_path: Path,
):
    """Fuse LoRA adapter into base model."""
    print(f"\nFusing adapter into model...")

    cmd = [
        "mlx_lm.fuse",
        "--model", model,
        "--adapter-path", str(adapter_path),
        "--save-path", str(output_path),
    ]

    print(f"Command: {' '.join(cmd)}\n")
    result = subprocess.run(cmd)

    if result.returncode != 0:
        print(f"Fuse failed with return code {result.returncode}")
        return False

    print(f"Fused model saved to: {output_path}")
    return True


def test_model(model_path: Path, prompt: str):
    """Test the trained model with a prompt."""
    print(f"\nTesting model: {model_path}")
    print(f"Prompt: {prompt}\n")

    cmd = [
        "mlx_lm.generate",
        "--model", str(model_path),
        "--prompt", prompt,
        "--max-tokens", "256",
    ]

    subprocess.run(cmd)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Train LoRA adapter with MLX")
    parser.add_argument("--model", type=str,
                        default="mlx-community/Qwen3-4B-Instruct-2507-4bit",
                        help="Base model (HuggingFace path or local)")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="Directory with train.jsonl and valid.jsonl")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Output directory for adapters and fused model")
    parser.add_argument("--iters", type=int, default=500,
                        help="Training iterations")
    parser.add_argument("--batch-size", type=int, default=4,
                        help="Batch size")
    parser.add_argument("--num-layers", type=int, default=8,
                        help="Number of layers to fine-tune")
    parser.add_argument("--lora-rank", type=int, default=8,
                        help="LoRA rank (lower = more regularization)")
    parser.add_argument("--lora-dropout", type=float, default=0.05,
                        help="LoRA dropout for regularization")
    parser.add_argument("--learning-rate", type=float, default=2e-5,
                        help="Learning rate")
    parser.add_argument("--fuse", action="store_true",
                        help="Fuse adapter into model after training")
    parser.add_argument("--test", action="store_true",
                        help="Test the model after training/fusing")
    parser.add_argument("--download", action="store_true",
                        help="Download model from HuggingFace first")
    args = parser.parse_args()

    # Set up paths
    base_dir = Path(__file__).parent.parent
    data_dir = Path(args.data_dir) if args.data_dir else base_dir / "data"
    output_dir = Path(args.output_dir) if args.output_dir else base_dir / "models"
    output_dir.mkdir(exist_ok=True)

    # Check prerequisites
    if not check_mlx_lm():
        sys.exit(1)

    # Check for training data
    train_file = data_dir / "train.jsonl"
    if not train_file.exists():
        print(f"Error: {train_file} not found")
        print("Run prepare_dataset.py first to create training data")
        sys.exit(1)

    # Count training examples
    with open(train_file) as f:
        train_count = sum(1 for _ in f)
    print(f"Training examples: {train_count}")

    valid_file = data_dir / "valid.jsonl"
    if valid_file.exists():
        with open(valid_file) as f:
            valid_count = sum(1 for _ in f)
        print(f"Validation examples: {valid_count}")

    # Download model if requested
    if args.download:
        download_model(args.model)

    # Train
    success = train_lora(
        model=args.model,
        data_dir=data_dir,
        output_dir=output_dir,
        iters=args.iters,
        batch_size=args.batch_size,
        num_layers=args.num_layers,
        lora_rank=args.lora_rank,
        lora_dropout=args.lora_dropout,
        learning_rate=args.learning_rate,
    )

    if not success:
        sys.exit(1)

    # Fuse if requested
    if args.fuse:
        fused_path = output_dir / "akriti-qwen3-4b"
        success = fuse_adapter(
            model=args.model,
            adapter_path=output_dir / "adapters",
            output_path=fused_path,
        )

        if success and args.test:
            test_model(
                fused_path,
                "What is the purpose of the workflows module in labcore?"
            )


if __name__ == "__main__":
    main()
