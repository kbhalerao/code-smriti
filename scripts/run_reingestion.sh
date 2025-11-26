#!/bin/bash

# Run Re-ingestion
# This script sets up the environment and runs the re-ingestion process

# Ensure we are in the project root
cd "$(dirname "$0")"

# Activate UV-managed virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    echo "Error: .venv not found. Please run 'uv venv .venv' first."
    exit 1
fi

# Run re-ingestion script
python3 2-initialize/reingest_all.py
