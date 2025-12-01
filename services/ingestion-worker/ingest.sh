#!/bin/bash
#
# Unified Ingestion Script for CodeSmriti V4
#
# Runs both code and documentation ingestion in sequence.
# Code ingestion creates: file_index, symbol_index, module_summary, repo_summary
# Doc ingestion creates: document (for .md, .rst, .txt files)
#
# Usage:
#   ./ingest.sh --repo owner/name     # Single repo
#   ./ingest.sh --all                 # All repos
#   ./ingest.sh --all --dry-run       # Preview mode
#   ./ingest.sh --all --skip-existing # Resume after failure
#   ./ingest.sh --all --code-only     # Skip doc ingestion
#   ./ingest.sh --all --docs-only     # Skip code ingestion
#
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Parse arguments
REPO=""
ALL=false
DRY_RUN=false
SKIP_EXISTING=false
CODE_ONLY=false
DOCS_ONLY=false
NO_LLM=false
NO_EMBEDDINGS=false
CONCURRENCY=4
LLM_PROVIDER="lmstudio"
OUTPUT=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --repo)
            REPO="$2"
            shift 2
            ;;
        --all)
            ALL=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --skip-existing)
            SKIP_EXISTING=true
            shift
            ;;
        --code-only)
            CODE_ONLY=true
            shift
            ;;
        --docs-only)
            DOCS_ONLY=true
            shift
            ;;
        --no-llm)
            NO_LLM=true
            shift
            ;;
        --no-embeddings)
            NO_EMBEDDINGS=true
            shift
            ;;
        --concurrency)
            CONCURRENCY="$2"
            shift 2
            ;;
        --llm-provider)
            LLM_PROVIDER="$2"
            shift 2
            ;;
        --output)
            OUTPUT="$2"
            shift 2
            ;;
        -h|--help)
            echo "CodeSmriti V4 Unified Ingestion"
            echo ""
            echo "Usage: ./ingest.sh [OPTIONS]"
            echo ""
            echo "Target (one required):"
            echo "  --repo OWNER/NAME    Ingest single repository"
            echo "  --all                Ingest all repos in REPOS_PATH"
            echo ""
            echo "Options:"
            echo "  --dry-run            Preview without writing to database"
            echo "  --skip-existing      Skip repos with existing V4 documents"
            echo "  --code-only          Run only code ingestion (skip docs)"
            echo "  --docs-only          Run only doc ingestion (skip code)"
            echo "  --no-llm             Disable LLM (basic summaries only)"
            echo "  --no-embeddings      Disable embedding generation"
            echo "  --concurrency N      Parallel file processors (default: 4)"
            echo "  --llm-provider P     LLM backend: lmstudio or ollama"
            echo "  --output FILE        Save code ingestion results to JSON"
            echo ""
            echo "Examples:"
            echo "  ./ingest.sh --repo kbhalerao/labcore --dry-run"
            echo "  ./ingest.sh --all --skip-existing"
            echo "  ./ingest.sh --all --code-only --output results.json"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate arguments
if [[ -z "$REPO" && "$ALL" == "false" ]]; then
    echo "Error: Must specify --repo or --all"
    exit 1
fi

if [[ -n "$REPO" && "$ALL" == "true" ]]; then
    echo "Error: Cannot specify both --repo and --all"
    exit 1
fi

if [[ "$CODE_ONLY" == "true" && "$DOCS_ONLY" == "true" ]]; then
    echo "Error: Cannot specify both --code-only and --docs-only"
    exit 1
fi

# Build code ingestion command
build_code_cmd() {
    local cmd="python ingest_v4.py"

    if [[ -n "$REPO" ]]; then
        cmd="$cmd --repo $REPO"
    else
        cmd="$cmd --all"
    fi

    [[ "$DRY_RUN" == "true" ]] && cmd="$cmd --dry-run"
    [[ "$SKIP_EXISTING" == "true" ]] && cmd="$cmd --skip-existing"
    [[ "$NO_LLM" == "true" ]] && cmd="$cmd --no-llm"
    [[ "$NO_EMBEDDINGS" == "true" ]] && cmd="$cmd --no-embeddings"
    cmd="$cmd --concurrency $CONCURRENCY"
    cmd="$cmd --llm-provider $LLM_PROVIDER"
    [[ -n "$OUTPUT" ]] && cmd="$cmd --output $OUTPUT"

    echo "$cmd"
}

# Build doc ingestion command
build_docs_cmd() {
    local cmd="python v4/ingest_docs.py"

    [[ -n "$REPO" ]] && cmd="$cmd --repo $REPO"
    [[ "$DRY_RUN" == "true" ]] && cmd="$cmd --dry-run"

    echo "$cmd"
}

echo "========================================"
echo "CodeSmriti V4 Unified Ingestion"
echo "========================================"
echo ""

# Phase 1: Code Ingestion
if [[ "$DOCS_ONLY" != "true" ]]; then
    echo "Phase 1: Code Ingestion"
    echo "----------------------------------------"
    CODE_CMD=$(build_code_cmd)
    echo "Running: $CODE_CMD"
    echo ""
    eval "$CODE_CMD"
    echo ""
    echo "Code ingestion complete."
    echo ""
fi

# Phase 2: Documentation Ingestion
if [[ "$CODE_ONLY" != "true" ]]; then
    echo "Phase 2: Documentation Ingestion"
    echo "----------------------------------------"
    DOCS_CMD=$(build_docs_cmd)
    echo "Running: $DOCS_CMD"
    echo ""
    eval "$DOCS_CMD"
    echo ""
    echo "Documentation ingestion complete."
    echo ""
fi

echo "========================================"
echo "Unified ingestion complete!"
echo "========================================"
