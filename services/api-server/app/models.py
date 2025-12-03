"""
Pydantic models for API request/response and database documents.
"""

from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, EmailStr, Field


# ============================================================================
# Database Document Models
# ============================================================================

class RepoInfo(BaseModel):
    """Repository information in user document."""
    repo_id: str
    added_at: str
    last_synced: Optional[str] = None
    chunk_count: int = 0
    status: Literal["pending", "syncing", "synced", "error"] = "pending"
    sync_error: Optional[str] = None


class UserDocument(BaseModel):
    """User document stored in users bucket."""
    type: Literal["user"] = "user"
    user_id: str
    email: EmailStr
    password_hash: str
    github_pat_encrypted: Optional[str] = None
    repos: list[RepoInfo] = []
    quota_max_repos: int = 10
    quota_max_chunks: int = 100000
    created_at: str
    updated_at: str
    last_login: Optional[str] = None


class JobProgress(BaseModel):
    """Job progress information."""
    total_files: int = 0
    processed_files: int = 0
    total_chunks: int = 0
    current_file: Optional[str] = None


class IngestionJobDocument(BaseModel):
    """Ingestion job document stored in ingestion_jobs bucket."""
    type: Literal["ingestion_job"] = "ingestion_job"
    job_id: str
    user_id: str
    repo_id: str
    status: Literal["queued", "running", "completed", "failed"] = "queued"
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    progress: JobProgress = Field(default_factory=JobProgress)
    error: Optional[str] = None


class ChunkMetadata(BaseModel):
    """Chunk metadata."""
    repo_name: str
    file_type: str
    file_size: int
    last_modified: str
    commit_sha: Optional[str] = None


class CodeChunkDocument(BaseModel):
    """Code chunk document stored in code_kosha bucket."""
    type: Literal["chunk"] = "chunk"
    user_id: str
    repo_id: str
    file_path: str
    chunk_index: int
    content: str  # Unified content field (was code_text)
    language: str
    start_line: int
    end_line: int
    embedding: list[float]
    metadata: ChunkMetadata


# ============================================================================
# API Request/Response Models
# ============================================================================

# Auth
class LoginRequest(BaseModel):
    """Login request body."""
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    """Register request body."""
    email: EmailStr
    password: str = Field(min_length=8)


class TokenResponse(BaseModel):
    """Authentication token response."""
    access_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    """Request to refresh an access token."""
    refresh_token: str


class AuthResponse(BaseModel):
    """Authentication response with user data."""
    success: bool = True
    token: str
    refresh_token: str
    user: "SafeUserInfo"


# User
class SafeUserInfo(BaseModel):
    """Safe user info (no sensitive data) for API responses."""
    user_id: str
    email: EmailStr
    repos: list[RepoInfo]
    quota_max_repos: int
    quota_max_chunks: int
    created_at: str
    last_login: Optional[str]


class UpdateGitHubPATRequest(BaseModel):
    """Request to update GitHub PAT."""
    github_pat: str


# Repos
class AddRepoRequest(BaseModel):
    """Request to add a repository."""
    repo_id: str = Field(pattern=r'^[\w.-]+/[\w.-]+$')


class AddRepoResponse(BaseModel):
    """Response after adding a repository."""
    success: bool = True
    job_id: str
    message: str


class DeleteRepoResponse(BaseModel):
    """Response after deleting a repository."""
    success: bool = True
    message: str
    deleted_chunks: int


# Search
class SearchRequest(BaseModel):
    """Search request body."""
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=50)
    repo_id: Optional[str] = None


class SearchResult(BaseModel):
    """Individual search result."""
    chunk: CodeChunkDocument
    score: float
    highlights: Optional[list[str]] = None


class SearchResponse(BaseModel):
    """Search results response."""
    success: bool = True
    results: list[SearchResult]
    query: str
    total_results: int


# Generic responses
class MessageResponse(BaseModel):
    """Generic success message response."""
    success: bool = True
    message: str


class ErrorResponse(BaseModel):
    """Error response."""
    success: bool = False
    error: str
