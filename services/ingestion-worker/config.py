"""
Configuration for Ingestion Worker
"""

import os
from pydantic_settings import BaseSettings


class WorkerConfig(BaseSettings):
    """Worker configuration loaded from environment"""

    # Couchbase Configuration
    couchbase_host: str = os.getenv("COUCHBASE_HOST", "localhost")
    couchbase_port: int = int(os.getenv("COUCHBASE_PORT", "8091"))
    couchbase_username: str = os.getenv("COUCHBASE_USERNAME", "Administrator")
    couchbase_password: str = os.getenv("COUCHBASE_PASSWORD", "")
    couchbase_bucket: str = os.getenv("COUCHBASE_BUCKET", "code_memory")

    # Repository Storage Path
    repos_path: str = os.getenv("REPOS_PATH", os.path.expanduser("~/codesmriti-repos"))

    # Embedding Configuration
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "local")  # "local" or "ollama"
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    # Model is cached locally after first download
    embedding_dimensions: int = 768

    # Incremental Update Configuration
    enable_incremental_updates: bool = os.getenv("ENABLE_INCREMENTAL_UPDATES", "false").lower() == "true"

    # Async Pipeline Configuration
    max_concurrent_files: int = int(os.getenv("MAX_CONCURRENT_FILES", "10"))  # Process N files at once
    max_parsing_threads: int = int(os.getenv("MAX_PARSING_THREADS", "4"))     # Thread pool for CPU-bound parsing
    embedding_batch_size: int = int(os.getenv("EMBEDDING_BATCH_SIZE", "128")) # Chunks per embedding batch

    # GitHub Configuration
    github_token: str = os.getenv("GITHUB_TOKEN", "").strip()
    github_repos: str = os.getenv("GITHUB_REPOS", "")

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Parsing Configuration
    supported_code_extensions: list = [
        ".py", ".js", ".ts", ".tsx", ".jsx",  # Python, JavaScript, TypeScript
        ".svelte", ".vue",                     # Component frameworks
        ".html", ".htm",                       # HTML
        ".css", ".scss", ".sass",              # Stylesheets
        ".sql",                                # SQL
    ]
    supported_doc_extensions: list = [
        ".md", ".rst",                    # Markdown, reStructuredText (Sphinx)
        ".txt",                           # Plain text
        ".json", ".yaml", ".yml",         # Configuration/data
        ".toml", ".ini", ".cfg"           # Config files
    ]
    max_chunk_size: int = 2000  # characters
    min_chunk_size: int = 50  # characters

    class Config:
        env_file = ".env"
        case_sensitive = False
        extra = 'ignore'  # Ignore extra env vars (e.g., MCP server configs)
