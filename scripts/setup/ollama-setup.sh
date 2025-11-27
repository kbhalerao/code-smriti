#!/bin/bash

# Ollama Setup Script for CodeSmriti
# Installs Ollama (if needed) and pulls recommended models for code understanding
# Smriti (स्मृति): Sanskrit for "memory, remembrance"

set -e

echo "=== CodeSmriti Ollama Setup (Mac M3 Ultra) ==="

# Check if Ollama is installed
if ! command -v ollama &> /dev/null; then
    echo "Ollama not found. Installing Ollama..."
    curl -fsSL https://ollama.ai/install.sh | sh
    echo "✓ Ollama installed"
else
    echo "✓ Ollama already installed at $(which ollama)"
    echo "  Version: $(ollama --version)"
fi

# Check if Ollama is running
if ! curl -s http://localhost:11434/api/version > /dev/null 2>&1; then
    echo ""
    echo "Ollama is not running. Starting Ollama..."
    echo "Please run in another terminal:"
    echo "  ollama serve"
    echo ""
    echo "Then re-run this script."
    exit 1
fi

echo "✓ Ollama is running on http://localhost:11434"

# Pull recommended models for code understanding
echo ""
echo "Pulling recommended models for code understanding..."
echo "(This may take a while depending on your internet connection)"
echo ""

# Model 1: CodeLlama 13B - excellent for code explanation and generation
echo "[1/3] Pulling codellama:13b (7.4 GB)..."
ollama pull codellama:13b
echo "✓ codellama:13b ready"

# Model 2: DeepSeek Coder 6.7B - specialized for code understanding
echo "[2/3] Pulling deepseek-coder:6.7b (3.8 GB)..."
ollama pull deepseek-coder:6.7b
echo "✓ deepseek-coder:6.7b ready"

# Model 3: Mistral 7B - good general-purpose model for organization tasks
echo "[3/3] Pulling mistral:7b (4.1 GB)..."
ollama pull mistral:7b
echo "✓ mistral:7b ready"

echo ""
echo "=== Optional: Advanced Models ==="
echo "For even better performance with 256GB RAM, consider:"
echo ""
echo "  # DeepSeek Coder 33B - highest quality code understanding"
echo "  ollama pull deepseek-coder:33b"
echo ""
echo "  # CodeLlama 70B - top-tier code generation (quantized)"
echo "  ollama pull codellama:70b-instruct-q4_K_M"
echo ""
echo "  # Qwen 2.5 Coder 32B - excellent multilingual code support"
echo "  ollama pull qwen2.5-coder:32b"
echo ""

# List all models
echo "=== Installed Models ==="
ollama list

echo ""
echo "✓ Ollama setup complete!"
echo ""
echo "The following models are ready for CodeSmriti:"
echo "  • codellama:13b - Primary model for code explanation"
echo "  • deepseek-coder:6.7b - Fast code analysis"
echo "  • mistral:7b - General purpose for organization tasks"
echo ""
echo "To test a model:"
echo "  ollama run codellama:13b"
echo ""
echo "Ollama API is accessible at: http://localhost:11434"
