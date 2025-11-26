FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY ./4-consume/api-server/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared lib
COPY ./lib/code-fetcher /app/lib/code-fetcher

# Copy application code
COPY ./4-consume/api-server/app/ ./app/

# Create logs directory
RUN mkdir -p logs

# Expose port
EXPOSE 8000

# Run with production settings (no auto-reload)
# --timeout-keep-alive: connection keep-alive timeout
# Using single worker to avoid memory issues with embedding model loading
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "120"]
