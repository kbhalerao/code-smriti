"""FastAPI routes for LLM chat endpoint"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from loguru import logger

from app.dependencies import get_current_user
from app.database.couchbase_client import CouchbaseClient
from app.chat.simple_agent import SimpleRAGAgent, ChatResponse
from app.config import settings


router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """Request model for chat endpoint"""

    query: str = Field(
        description="User's query or question",
        min_length=1,
        max_length=2000
    )

    tenant_id: Optional[str] = Field(
        default=None,
        description="Tenant ID (defaults to user's tenant)"
    )


class ChatResponseAPI(BaseModel):
    """API response model for chat endpoint"""

    answer: str = Field(description="Generated answer")

    intent: dict = Field(description="Intent classification details")

    sources: List[dict] = Field(
        default_factory=list,
        description="Source code chunks used"
    )

    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata"
    )


@router.post("/", response_model=ChatResponseAPI)
async def chat(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user)
) -> ChatResponseAPI:
    """
    Chat endpoint with RAG-enriched responses.

    Two-phase architecture:
    1. Intent Classification: Determines if query can be answered from code
    2. RAG Research: Searches code and generates contextual answer

    Requires authentication via JWT token in Authorization header.

    Example:
        ```bash
        curl -X POST http://localhost:8000/api/chat/ \\
          -H "Authorization: Bearer YOUR_JWT_TOKEN" \\
          -H "Content-Type: application/json" \\
          -d '{"query": "How does authentication work in the API?"}'
        ```

    Args:
        request: Chat request with query
        current_user: Authenticated user (from JWT token)

    Returns:
        ChatResponseAPI with answer, intent classification, and sources

    Raises:
        HTTPException: If query processing fails
    """
    try:
        # Determine tenant ID
        tenant_id = request.tenant_id or current_user.get("tenant_id", "code_kosha")

        logger.info(
            f"Chat request from user={current_user.get('username')} "
            f"tenant={tenant_id} query='{request.query}'"
        )

        # Initialize database and agent
        db = CouchbaseClient()
        agent = SimpleRAGAgent(
            db=db,
            tenant_id=tenant_id,
            ollama_host=settings.ollama_host
        )

        try:
            # Process the query (two-phase: intent → RAG)
            response = await agent.chat(query=request.query)

            # Convert to API response format
            return ChatResponseAPI(
                answer=response.answer,
                intent=response.metadata.get("intent", {}),
                sources=response.sources,
                metadata=response.metadata
            )
        finally:
            await agent.close()

    except Exception as e:
        logger.error(f"Chat request failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process chat request: {str(e)}"
        )


@router.get("/health")
async def chat_health():
    """Health check for chat service"""
    return {
        "status": "healthy",
        "service": "chat",
        "ollama_host": settings.ollama_host
    }


@router.post("/test", response_model=ChatResponseAPI)
async def chat_test(request: ChatRequest) -> ChatResponseAPI:
    """
    Test chat endpoint WITHOUT authentication (for internal testing only)

    Example:
        ```bash
        curl -X POST http://localhost:8000/api/chat/test \\
          -H "Content-Type: application/json" \\
          -d '{"query": "How does authentication work?"}'
        ```
    """
    try:
        tenant_id = "code_kosha"
        logger.info(f"Test chat request: query='{request.query}'")

        db = CouchbaseClient()
        agent = SimpleRAGAgent(
            db=db,
            tenant_id=tenant_id,
            ollama_host=settings.ollama_host
        )

        try:
            response = await agent.chat(query=request.query)

            return ChatResponseAPI(
                answer=response.answer,
                intent=response.metadata.get("intent", {}),
                sources=response.sources,
                metadata=response.metadata
            )
        finally:
            await agent.close()

    except Exception as e:
        logger.error(f"Test chat failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process chat request: {str(e)}"
        )
