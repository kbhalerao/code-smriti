FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including git for cloning repos
RUN apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download embedding model and tree-sitter languages
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('nomic-ai/nomic-embed-text-v1.5')"
RUN python -c "from tree_sitter_languages import get_parser; get_parser('python'); get_parser('javascript'); get_parser('typescript')"

# Copy application code
COPY . .

# Create non-root user
RUN useradd -m -u 1000 worker && chown -R worker:worker /app
RUN mkdir -p /repos && chown -R worker:worker /repos
USER worker

# Run the ingestion worker
CMD ["python", "worker.py"]
