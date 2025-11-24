"""FastAPI routes for LLM chat endpoint with PydanticAI RAG"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from loguru import logger

from app.dependencies import get_current_user, get_db, get_rag_agent_wrapper
from app.database.couchbase_client import CouchbaseClient
from app.chat.pydantic_rag_agent import CodeSmritiRAGAgent, ConversationMessage
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

    conversation_history: Optional[List[dict]] = Field(
        default=None,
        description="Previous conversation messages for context"
    )

    stream: bool = Field(
        default=False,
        description="Enable streaming response"
    )


class ChatResponseAPI(BaseModel):
    """API response model for chat endpoint"""

    answer: str = Field(description="Generated answer in markdown format")

    metadata: dict = Field(
        default_factory=dict,
        description="Additional metadata (tools used, timing, etc.)"
    )


@router.post("/")
async def chat(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Chat endpoint with RAG-enriched responses using PydanticAI.

    Features:
    - Intent validation and scope checking
    - Vector search with Couchbase FTS + kNN
    - Tool-calling architecture for flexible search
    - Conversation history support for context
    - Streaming and non-streaming modes
    - High-quality markdown narratives with code blocks

    Requires authentication via JWT token in Authorization header.

    Example (non-streaming):
        ```bash
        curl -X POST http://localhost:8000/api/chat/ \\
          -H "Authorization: Bearer YOUR_JWT_TOKEN" \\
          -H "Content-Type: application/json" \\
          -d '{
            "query": "How does authentication work in the API?",
            "stream": false
          }'
        ```

    Example (streaming):
        ```bash
        curl -X POST http://localhost:8000/api/chat/ \\
          -H "Authorization: Bearer YOUR_JWT_TOKEN" \\
          -H "Content-Type: application/json" \\
          -d '{
            "query": "Show me examples of vector search",
            "stream": true,
            "conversation_history": [
              {"role": "user", "content": "What repos do we have?"},
              {"role": "assistant", "content": "We have..."}
            ]
          }'
        ```

    Args:
        request: Chat request with query, optional history, stream flag
        current_user: Authenticated user (from JWT token)
        db: Shared database client (injected)

    Returns:
        StreamingResponse if stream=true, else ChatResponseAPI

    Raises:
        HTTPException: If query processing fails
    """
    try:
        # Determine tenant ID
        tenant_id = request.tenant_id or current_user.get("tenant_id", "code_kosha")

        logger.info(
            f"Chat request from user={current_user.get('username')} "
            f"tenant={tenant_id} stream={request.stream} query='{request.query[:100]}'"
        )

        # Parse conversation history
        conversation_history = []
        if request.conversation_history:
            conversation_history = [
                ConversationMessage(role=msg["role"], content=msg["content"])
                for msg in request.conversation_history
            ]

        # Initialize PydanticAI agent (now works with LMStudio's OpenAI-compatible endpoint)
        agent = CodeSmritiRAGAgent(
            db=db,
            tenant_id=tenant_id,
            ollama_host=settings.ollama_host,
            llm_model=settings.llm_model_name,
            embedding_model_name=settings.embedding_model_name,
            conversation_history=conversation_history
        )

        # Streaming response
        if request.stream:
            async def stream_generator():
                """Generator for streaming response."""
                try:
                    async for chunk in agent.chat_stream(request.query):
                        yield chunk
                except Exception as e:
                    logger.error(f"Streaming error: {e}", exc_info=True)
                    yield f"\n\n[Error: {str(e)}]"

            return StreamingResponse(
                stream_generator(),
                media_type="text/plain"
            )

        # Non-streaming response
        else:
            answer = await agent.chat(request.query)

            return ChatResponseAPI(
                answer=answer,
                metadata={
                    "tenant_id": tenant_id,
                    "conversation_length": len(agent.conversation_history)
                }
            )

    except Exception as e:
        logger.error(f"Chat request failed: {str(e).replace('{', '{{').replace('}', '}}')}", exc_info=True)
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


@router.post("/test")
async def chat_test(request: ChatRequest, db: CouchbaseClient = Depends(get_db)):
    """
    Test chat endpoint WITHOUT authentication (for internal testing only).

    Supports both streaming and non-streaming modes.

    Example (non-streaming):
        ```bash
        curl -X POST http://localhost:8000/api/chat/test \\
          -H "Content-Type: application/json" \\
          -d '{"query": "How does authentication work?", "stream": false}'
        ```

    Example (streaming):
        ```bash
        curl -X POST http://localhost:8000/api/chat/test \\
          -H "Content-Type: application/json" \\
          -d '{"query": "Show me vector search examples", "stream": true}'
        ```
    """
    try:
        tenant_id = "code_kosha"
        logger.info(f"Test chat request: stream={request.stream} query='{request.query[:100]}'")

        # Parse conversation history
        conversation_history = []
        if request.conversation_history:
            conversation_history = [
                ConversationMessage(role=msg["role"], content=msg["content"])
                for msg in request.conversation_history
            ]

        # Initialize PydanticAI agent (now works with LMStudio's OpenAI-compatible endpoint)
        agent = CodeSmritiRAGAgent(
            db=db,
            tenant_id=tenant_id,
            ollama_host=settings.ollama_host,
            llm_model=settings.llm_model_name,
            embedding_model_name=settings.embedding_model_name,
            conversation_history=conversation_history
        )

        # Streaming response
        if request.stream:
            async def stream_generator():
                """Generator for streaming response."""
                try:
                    async for chunk in agent.chat_stream(request.query):
                        yield chunk
                except Exception as e:
                    logger.error(f"Streaming error: {e}", exc_info=True)
                    yield f"\n\n[Error: {str(e)}]"

            return StreamingResponse(
                stream_generator(),
                media_type="text/plain"
            )

        # Non-streaming response
        else:
            answer = await agent.chat(request.query)

            return ChatResponseAPI(
                answer=answer,
                metadata={
                    "tenant_id": tenant_id,
                    "conversation_length": len(agent.conversation_history)
                }
            )

    except Exception as e:
        logger.error(f"Test chat failed: {str(e).replace('{', '{{').replace('}', '}}')}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process chat request: {str(e)}"
        )


class SearchRequest(BaseModel):
    """Request model for search-only endpoint"""
    query: Optional[str] = Field(default=None, description="Semantic vector search query")
    text_query: Optional[str] = Field(default=None, description="Keyword/BM25 text search query")
    limit: int = Field(default=5, ge=1, le=20, description="Number of results")
    repo_filter: Optional[str] = Field(default=None, description="Filter by repository")
    file_path_pattern: Optional[str] = Field(default=None, description="File path pattern (e.g., '*.py')")
    doc_type: str = Field(default="code_chunk", description="Document type: code_chunk, document, or commit")


@router.post("/search")
async def search_code_only(request: SearchRequest, db: CouchbaseClient = Depends(get_db)):
    """
    Raw search endpoint that returns documents without LLM processing.
    Supports hybrid text+vector search and filtering.

    Use this to evaluate search quality before RAG processing.

    Examples:
        Vector search:
          {"query": "background consumer processing"}

        Text search:
          {"text_query": "BackgroundConsumer class"}

        Hybrid search:
          {"query": "background consumer", "text_query": "BackgroundConsumer"}

        With filters:
          {"query": "authentication", "repo_filter": "kbhalerao/labcore", "file_path_pattern": "*.py"}
    """
    try:
        tenant_id = "code_kosha"
        logger.info(f"Search request: vector={request.query} text={request.text_query}")

        # Initialize context for search
        from app.chat.manual_rag_agent import RAGContext, search_code_tool
        from app.chat.pydantic_rag_agent import get_embedding_model, get_http_client

        embedding_model = get_embedding_model(settings.embedding_model_name)
        http_client = get_http_client()

        ctx = RAGContext(
            db=db,
            tenant_id=tenant_id,
            ollama_host=settings.ollama_host,
            http_client=http_client,
            embedding_model=embedding_model
        )

        # Call search with all parameters
        results = await search_code_tool(
            ctx,
            query=request.query,
            text_query=request.text_query,
            limit=request.limit,
            repo_filter=request.repo_filter,
            file_path_pattern=request.file_path_pattern,
            doc_type=request.doc_type
        )

        search_mode = "hybrid" if (request.query and request.text_query) else ("text" if request.text_query else "vector")

        return {
            "query": request.query,
            "text_query": request.text_query,
            "search_mode": search_mode,
            "filters": {
                "repo": request.repo_filter,
                "file_path": request.file_path_pattern,
                "doc_type": request.doc_type
            },
            "results": results,
            "count": len(results)
        }

    except Exception as e:
        logger.error(f"Search failed: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Search failed: {str(e)}"
        )
