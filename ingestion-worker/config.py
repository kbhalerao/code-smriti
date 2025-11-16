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

    # Embedding Configuration
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    embedding_dimensions: int = 768

    # GitHub Configuration
    github_token: str = os.getenv("GITHUB_TOKEN", "")
    github_repos: str = os.getenv("GITHUB_REPOS", "")

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    # Parsing Configuration
    supported_code_extensions: list = [".py", ".js", ".ts", ".tsx", ".jsx"]
    supported_doc_extensions: list = [".md", ".txt", ".json", ".yaml", ".yml"]
    max_chunk_size: int = 2000  # characters
    min_chunk_size: int = 50  # characters

    class Config:
        env_file = ".env"
        case_sensitive = False
