FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the resolved requirements (see the header of requirements.lock)
COPY ./services/api-server/requirements.lock .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.lock

# Copy shared lib (code-fetcher is now in lib/)
COPY ./lib/code-fetcher /app/lib/code-fetcher

# Copy application code
COPY ./services/api-server/app/ ./app/

# Create logs directory
RUN mkdir -p logs

# Expose port
EXPOSE 8000

# Run with production settings (no auto-reload)
# --timeout-keep-alive: connection keep-alive timeout
# Single worker: the API holds per-process state (shared HTTP client, Couchbase cluster)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "120"]
