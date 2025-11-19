"""
Configuration management for TotalRecall MCP Server
"""

import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment"""

    # Couchbase Configuration
    couchbase_host: str = os.getenv("COUCHBASE_HOST", "localhost")
    couchbase_port: int = int(os.getenv("COUCHBASE_PORT", "8091"))
    couchbase_username: str = os.getenv("COUCHBASE_USERNAME", "Administrator")
    couchbase_password: str = os.getenv("COUCHBASE_PASSWORD", "")
    couchbase_bucket: str = os.getenv("COUCHBASE_BUCKET", "code_memory")

    # Ollama Configuration
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "codellama:13b")

    # Embedding Configuration
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
    embedding_model_revision: str = os.getenv("EMBEDDING_MODEL_REVISION", "main")
    embedding_dimensions: int = 768

    # JWT Authentication
    jwt_secret: str = os.getenv("JWT_SECRET", "change-this-secret-in-production")
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24

    # Rate Limiting
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))

    # Vector Search
    vector_search_top_k: int = int(os.getenv("VECTOR_SEARCH_TOP_K", "10"))
    similarity_threshold: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.7"))

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    class Config:
        env_file = ".env"
        case_sensitive = False


# Global settings instance
settings = Settings()
