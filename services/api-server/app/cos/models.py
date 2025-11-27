"""Pydantic models for Chief of Staff (personal productivity) API."""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DocType(str, Enum):
    """Document types for Chief of Staff."""
    idea = "idea"
    task = "task"
    note = "note"
    context = "context"
    project = "project"
    message = "message"


class Priority(str, Enum):
    """Priority levels."""
    high = "high"
    medium = "medium"
    low = "low"


class Status(str, Enum):
    """Document status."""
    inbox = "inbox"
    todo = "todo"
    in_progress = "in-progress"
    blocked = "blocked"
    done = "done"
    archived = "archived"


class CaptureMode(str, Enum):
    """How the document was captured."""
    explicit = "explicit"
    incremental = "incremental"
    batch = "batch"


class SourceInfo(BaseModel):
    """Source metadata for document."""

    client: str = Field(default="self", description="Client: claude-code, cli, telegram, api, self")
    project: Optional[str] = Field(None, description="Auto-detected project name")
    branch: Optional[str] = Field(None, description="Git branch")
    files: Optional[list[str]] = Field(None, description="Files touched")
    session_id: Optional[str] = Field(None, description="Session identifier")
    capture_mode: Optional[CaptureMode] = Field(
        None, description="How this was captured"
    )


# --- Request Models ---


class CreateDocRequest(BaseModel):
    """Create a new document."""

    doc_type: DocType
    content: str = Field(..., min_length=1, max_length=10000)
    title: Optional[str] = Field(None, max_length=200)
    tags: list[str] = Field(default_factory=list)
    priority: Optional[Priority] = None
    status: Status = Field(default=Status.inbox)
    due_date: Optional[str] = Field(None, description="ISO8601 date")
    project_id: Optional[str] = Field(None, description="Link to project doc")
    parent_id: Optional[str] = Field(None, description="Parent doc (for subtasks)")
    source: Optional[SourceInfo] = None
    metadata: dict = Field(default_factory=dict)


class UpdateDocRequest(BaseModel):
    """Update an existing document."""

    content: Optional[str] = Field(None, max_length=10000)
    title: Optional[str] = Field(None, max_length=200)
    tags: Optional[list[str]] = None
    priority: Optional[Priority] = None
    status: Optional[Status] = None
    due_date: Optional[str] = None
    metadata: Optional[dict] = None


class SaveContextRequest(BaseModel):
    """Save a context snapshot."""

    project: Optional[str] = None
    summary: str = Field(..., max_length=5000)
    key_topics: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


# --- Response Models ---


class DocResponse(BaseModel):
    """Single document response."""

    id: str
    doc_type: DocType
    user_id: str
    content: str
    title: Optional[str]
    tags: list[str]
    priority: Optional[Priority]
    status: Status
    due_date: Optional[str]
    project_id: Optional[str]
    parent_id: Optional[str]
    linked_ids: list[str]
    source: Optional[SourceInfo]
    metadata: dict
    created_at: datetime
    updated_at: datetime


class DocsListResponse(BaseModel):
    """List of documents response."""

    items: list[DocResponse]
    total: int
    limit: int
    offset: int


class TagInfo(BaseModel):
    """Tag with count."""

    tag: str
    count: int


class TagsResponse(BaseModel):
    """All tags response."""

    tags: list[TagInfo]
    total_tags: int


class StatsResponse(BaseModel):
    """Statistics response."""

    total_docs: int
    by_doc_type: dict[str, int]
    by_status: dict[str, int]
    by_priority: dict[str, int]
    recent_activity: int  # docs updated in last 24h


class ContextResponse(BaseModel):
    """Context snapshot response."""

    id: str
    project: Optional[str]
    summary: str
    key_topics: list[str]
    files_modified: list[str]
    open_questions: list[str]
    created_at: datetime


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    couchbase_connected: bool
    bucket: str
    user_scope: str
