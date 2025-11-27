"""
FastAPI dependencies for authentication and authorization.
"""

from typing import Annotated, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth.utils import verify_token
from .database.couchbase_client import CouchbaseClient, get_cluster
from .chat.pydantic_rag_agent import CodeSmritiRAGAgent
from .config import settings

# HTTP Bearer token scheme
security = HTTPBearer()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]
) -> dict:
    """
    Dependency to get and verify the current authenticated user from JWT.

    Args:
        credentials: HTTP Bearer credentials from Authorization header

    Returns:
        dict: Decoded JWT payload containing user_id and email

    Raises:
        HTTPException: 401 if token is invalid or expired
    """
    token = credentials.credentials

    payload = verify_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


# Type alias for dependency injection
CurrentUser = Annotated[dict, Depends(get_current_user)]


# ============================================================================
# Database Dependencies
# ============================================================================

_db_client: Optional[CouchbaseClient] = None


def get_db() -> CouchbaseClient:
    """
    Dependency to get shared Couchbase client (singleton).

    Returns:
        CouchbaseClient: Shared database client instance
    """
    global _db_client
    if _db_client is None:
        _db_client = CouchbaseClient()
    return _db_client


# ============================================================================
# RAG Agent Dependencies
# ============================================================================

def get_rag_agent_wrapper(
    current_user: CurrentUser,
    db: Annotated[CouchbaseClient, Depends(get_db)]
) -> CodeSmritiRAGAgent:
    """
    Dependency to create a RAG agent wrapper for the current request.

    This uses shared singleton resources (HTTP client, embedding model, PydanticAI agent)
    but creates a lightweight wrapper with per-request conversation state.

    Args:
        current_user: Current authenticated user
        db: Shared database client

    Returns:
        CodeSmritiRAGAgent: RAG agent wrapper with per-request state
    """
    tenant_id = current_user.get("tenant_id", settings.couchbase_bucket_code)

    return CodeSmritiRAGAgent(
        db=db,
        tenant_id=tenant_id,
        ollama_host=settings.ollama_host,
        llm_model=settings.llm_model_name,
        embedding_model_name=settings.embedding_model_name,
        conversation_history=[]
    )
