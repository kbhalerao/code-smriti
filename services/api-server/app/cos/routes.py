"""FastAPI router for Chief of Staff API.

Personal productivity endpoints: tasks, ideas, notes, context snapshots.
"""

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_current_user
from .db import CosDatabase, get_cos_db
from .models import (
    ContextResponse,
    CreateDocRequest,
    DocResponse,
    DocsListResponse,
    DocType,
    HealthResponse,
    Priority,
    SaveContextRequest,
    StatsResponse,
    Status,
    TagsResponse,
    UpdateDocRequest,
)

router = APIRouter(prefix="/cos", tags=["Chief of Staff"])


def get_user_id(
    current_user: Annotated[dict, Depends(get_current_user)],
    db: CosDatabase = Depends(get_cos_db),
) -> str:
    """Extract user ID (email) from JWT token, validate against users bucket."""
    # Get email from JWT payload
    user_id = current_user.get("email")

    if not user_id:
        raise HTTPException(
            status_code=401,
            detail="Invalid token: missing email claim"
        )

    # Validate user exists and ensure their scope is provisioned
    if not db.validate_user(user_id):
        raise HTTPException(
            status_code=401,
            detail=f"User '{user_id}' not found in users database"
        )

    return user_id


# --- Health ---


@router.get("/health", response_model=HealthResponse)
async def health_check(
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> HealthResponse:
    """CoS-specific health check."""
    from ..config import settings

    connected = db.is_connected()
    return HealthResponse(
        status="healthy" if connected else "unhealthy",
        couchbase_connected=connected,
        bucket=settings.couchbase_bucket_cos,
        user_scope=f"user_{user_id}",
    )


# --- Documents CRUD ---


@router.post("/docs", response_model=DocResponse, status_code=201)
async def create_document(
    request: CreateDocRequest,
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> DocResponse:
    """Create a new document (idea, task, note, etc.)."""
    return await db.create_document(user_id, request)


@router.get("/docs", response_model=DocsListResponse)
async def list_documents(
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
    doc_type: Annotated[Optional[DocType], Query()] = None,
    status: Annotated[Optional[Status], Query()] = None,
    priority: Annotated[Optional[Priority], Query()] = None,
    tags: Annotated[Optional[list[str]], Query()] = None,
    project: Annotated[Optional[str], Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort: Annotated[str, Query()] = "updated_at:desc",
    exclude_done: Annotated[bool, Query(description="Exclude done/archived items")] = True,
) -> DocsListResponse:
    """List documents with filters."""
    return await db.list_documents(
        user_id,
        doc_type=doc_type,
        status=status,
        priority=priority,
        tags=tags,
        project=project,
        limit=limit,
        offset=offset,
        sort=sort,
        exclude_done=exclude_done,
    )


@router.get("/docs/next", response_model=DocsListResponse)
async def get_next_actions(
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> DocsListResponse:
    """Get priority queue - high priority first, then by due date."""
    return await db.get_next_actions(user_id, limit=limit)


@router.get("/docs/inbox", response_model=DocsListResponse)
async def get_inbox(
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DocsListResponse:
    """Get inbox items (status=inbox)."""
    return await db.get_inbox(user_id, limit=limit)


@router.get("/docs/due", response_model=DocsListResponse)
async def get_due_soon(
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> DocsListResponse:
    """Get tasks with approaching due dates."""
    return await db.get_due_soon(user_id, days=days, limit=limit)


@router.get("/docs/{doc_id}", response_model=DocResponse)
async def get_document(
    doc_id: str,
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> DocResponse:
    """Get a single document by ID."""
    doc = await db.get_document(user_id, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.patch("/docs/{doc_id}", response_model=DocResponse)
async def update_document(
    doc_id: str,
    request: UpdateDocRequest,
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> DocResponse:
    """Update an existing document."""
    doc = await db.update_document(user_id, doc_id, request)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/docs/{doc_id}", status_code=204)
async def delete_document(
    doc_id: str,
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
    hard: Annotated[bool, Query()] = False,
) -> None:
    """Delete a document (soft delete by default - archives it)."""
    deleted = await db.delete_document(user_id, doc_id, hard=hard)
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")


# --- Project-scoped queries ---


@router.get("/projects/{project_name}/docs", response_model=DocsListResponse)
async def get_project_docs(
    project_name: str,
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DocsListResponse:
    """Get all docs for a project."""
    return await db.get_project_docs(user_id, project_name, limit=limit)


@router.get("/projects/{project_name}/recent", response_model=DocsListResponse)
async def get_project_recent(
    project_name: str,
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> DocsListResponse:
    """Get recent activity for a project."""
    return await db.get_project_recent(user_id, project_name, limit=limit)


# --- Tags ---


@router.get("/tags", response_model=TagsResponse)
async def get_tags(
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> TagsResponse:
    """Get all tags with counts."""
    tags = await db.get_tags(user_id)
    return TagsResponse(
        tags=[{"tag": t["tag"], "count": t["count"]} for t in tags],
        total_tags=len(tags),
    )


# --- Stats ---


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> StatsResponse:
    """Get statistics."""
    stats = await db.get_stats(user_id)
    return StatsResponse(**stats)


# --- Context ---


@router.get("/context", response_model=Optional[ContextResponse])
async def get_context(
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> Optional[ContextResponse]:
    """Get latest context snapshot."""
    doc = await db.get_latest_context(user_id)
    if not doc:
        return None
    return _doc_to_context_response(doc)


@router.get("/context/{project}", response_model=Optional[ContextResponse])
async def get_project_context(
    project: str,
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> Optional[ContextResponse]:
    """Get project-specific context snapshot."""
    doc = await db.get_latest_context(user_id, project=project)
    if not doc:
        return None
    return _doc_to_context_response(doc)


@router.post("/context", response_model=ContextResponse, status_code=201)
async def save_context(
    request: SaveContextRequest,
    db: Annotated[CosDatabase, Depends(get_cos_db)],
    user_id: Annotated[str, Depends(get_user_id)],
) -> ContextResponse:
    """Save a new context snapshot."""
    doc = await db.save_context(user_id, request)
    return _doc_to_context_response(doc)


def _doc_to_context_response(doc: DocResponse) -> ContextResponse:
    """Convert DocResponse to ContextResponse."""
    return ContextResponse(
        id=doc.id,
        project=doc.source.project if doc.source else None,
        summary=doc.content,
        key_topics=doc.metadata.get("key_topics", []),
        files_modified=doc.metadata.get("files_modified", []),
        open_questions=doc.metadata.get("open_questions", []),
        created_at=doc.created_at,
    )
