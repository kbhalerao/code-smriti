#!/bin/bash

# Run Re-ingestion
# This script sets up the environment and runs the re-ingestion process

# Ensure we are in the project root
cd "$(dirname "$0")"

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Creating virtual environment..."
    python3 -m venv venv
    source venv/bin/activate
    pip install -r lib/ingestion-worker/requirements.txt
fi

# Run re-ingestion script
python3 2-initialize/reingest_all.py
