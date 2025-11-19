"""
Application configuration loaded from environment variables.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment variables."""

    # Couchbase Configuration
    couchbase_host: str = "localhost"
    couchbase_port: int = 8091
    couchbase_user: str = "Administrator"
    couchbase_password: str

    # Couchbase Buckets
    couchbase_bucket_code: str = "code_kosha"
    couchbase_bucket_users: str = "users"
    couchbase_bucket_jobs: str = "ingestion_jobs"

    # Authentication & Security
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24

    # AES encryption for GitHub PATs
    aes_encryption_key: str

    # Ollama Configuration
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "nomic-embed-text"

    # Application
    app_name: str = "CodeSmriti API"
    api_version: str = "v1"
    log_level: str = "INFO"

    # CORS - Cloudflare origin
    cors_origins: list[str] = ["https://smriti.pages.dev", "http://localhost:5173"]
    cors_allow_credentials: bool = True

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Global settings instance
settings = Settings()
