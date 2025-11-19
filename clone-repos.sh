#!/bin/bash
# Clone GitHub repos with the naming convention expected by the ingestion worker
# Usage: ./clone-repos.sh owner/repo [owner/repo2 ...]

set -e

REPOS_DIR="repos"

if [ $# -eq 0 ]; then
    echo "Usage: $0 owner/repo [owner/repo2 ...]"
    echo "Example: $0 kbhalerao/labcore"
    exit 1
fi

mkdir -p "$REPOS_DIR"

for REPO in "$@"; do
    # Convert owner/repo to owner_repo
    DIR_NAME="${REPO//\//_}"
    CLONE_PATH="$REPOS_DIR/$DIR_NAME"

    if [ -d "$CLONE_PATH" ]; then
        echo "✓ $REPO already exists at $CLONE_PATH, pulling latest..."
        (cd "$CLONE_PATH" && git pull)
    else
        echo "Cloning $REPO to $CLONE_PATH..."
        git clone "https://github.com/$REPO.git" "$CLONE_PATH"
        echo "✓ Cloned $REPO"
    fi
done

echo ""
echo "✓ All repos ready!"
echo "You can now run: docker-compose up -d"
