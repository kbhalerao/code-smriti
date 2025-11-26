"""FastAPI routes for LLM chat endpoint with PydanticAI RAG"""
import os
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from loguru import logger
import httpx

from app.dependencies import get_current_user, get_db
from app.database.couchbase_client import CouchbaseClient
from app.chat.pydantic_rag_agent import (
    CodeSmritiRAGAgent,
    ConversationMessage,
    get_embedding_model,
    get_http_client,
)
from app.config import settings

# Add shared lib to path - works both locally and in Docker
# Docker: /app/lib/code-fetcher, Local: ../../../lib/code-fetcher
_lib_path_docker = Path("/app/lib/code-fetcher")
_lib_path_local = Path(__file__).parent.parent.parent.parent.parent / "lib" / "code-fetcher"
_lib_path = _lib_path_docker if _lib_path_docker.exists() else _lib_path_local
if str(_lib_path) not in sys.path:
    sys.path.insert(0, str(_lib_path))

from fetcher import CodeFetcher


router = APIRouter(prefix="/rag", tags=["CodeSmriti RAG"])


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


class SearchRequest(BaseModel):
    """Request model for vector search endpoint"""

    query: Optional[str] = Field(
        default=None,
        description="Semantic vector search query (natural language or code)"
    )

    text_query: Optional[str] = Field(
        default=None,
        description="Keyword/BM25 text search query on content field"
    )

    limit: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of results to return"
    )

    repo_filter: Optional[str] = Field(
        default=None,
        description="Filter by repository (format: owner/repo)"
    )

    file_path_pattern: Optional[str] = Field(
        default=None,
        description="File path pattern filter (e.g., '*.py', 'src/', 'test_')"
    )

    doc_type: str = Field(
        default="code_chunk",
        description="Document type: code_chunk, document, or commit"
    )

    tenant_id: Optional[str] = Field(
        default=None,
        description="Tenant ID (defaults to user's tenant)"
    )


class SearchResultItem(BaseModel):
    """Single search result item"""

    content: str = Field(description="Code or document content")
    repo_id: str = Field(description="Repository identifier")
    file_path: str = Field(description="File path in repository")
    language: str = Field(description="Programming language")
    score: float = Field(description="Relevance score")
    start_line: Optional[int] = Field(default=None, description="Start line number")
    end_line: Optional[int] = Field(default=None, description="End line number")
    doc_type: str = Field(default="code_chunk", description="Document type")


class SearchResponse(BaseModel):
    """Response model for vector search endpoint"""

    results: List[SearchResultItem] = Field(description="Search results")
    total: int = Field(description="Number of results returned")
    query: Optional[str] = Field(default=None, description="Vector search query")
    text_query: Optional[str] = Field(default=None, description="Text search query")
    search_mode: str = Field(description="Search mode: vector, text, or hybrid")
    filters: dict = Field(default_factory=dict, description="Applied filters")


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


@router.post("/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Vector/hybrid search endpoint - returns relevant documents without LLM synthesis.

    Supports three search modes:
    1. **Vector-only**: Semantic similarity search (provide `query`)
    2. **Text-only**: Keyword/BM25 search (provide `text_query` only)
    3. **Hybrid**: Combined text + vector scores (provide both)

    Requires authentication via JWT token in Authorization header.

    Examples:
        Vector search:
        ```json
        {"query": "authentication middleware"}
        ```

        Text search:
        ```json
        {"text_query": "BackgroundConsumer class"}
        ```

        Hybrid search:
        ```json
        {"query": "background processing", "text_query": "BackgroundConsumer"}
        ```

        With filters:
        ```json
        {
            "query": "authentication",
            "repo_filter": "owner/repo",
            "file_path_pattern": "*.py",
            "doc_type": "code_chunk"
        }
        ```
    """
    if not request.query and not request.text_query:
        raise HTTPException(
            status_code=400,
            detail="Either 'query' (vector search) or 'text_query' (text search) must be provided"
        )

    try:
        tenant_id = request.tenant_id or current_user.get("tenant_id", "code_kosha")
        search_mode = "hybrid" if (request.query and request.text_query) else ("text" if request.text_query else "vector")

        logger.info(
            f"Search request from user={current_user.get('username')} "
            f"tenant={tenant_id} mode={search_mode} query='{request.query or request.text_query}'"
        )

        # Get shared resources
        embedding_model = get_embedding_model(settings.embedding_model_name)
        http_client = get_http_client()

        # Oversample to handle filtering (FTS kNN pre-filtering can be unreliable)
        oversample_factor = 10
        fts_limit = max(request.limit * oversample_factor, 50)

        # Build FTS request
        fts_request = {
            "size": fts_limit,
            "fields": ["*"]
        }

        # Build filter conjuncts
        filter_conjuncts = [{"term": request.doc_type, "field": "type"}]

        if request.text_query:
            filter_conjuncts.append({"match": request.text_query, "field": "content"})

        if request.repo_filter:
            filter_conjuncts.append({"term": request.repo_filter, "field": "repo_id"})

        # Build filter object
        knn_filter = filter_conjuncts[0] if len(filter_conjuncts) == 1 else {"conjuncts": filter_conjuncts}

        # Add vector search if query provided
        if request.query:
            query_with_prefix = f"search_document: {request.query}"
            query_embedding = embedding_model.encode(
                query_with_prefix,
                normalize_embeddings=True
            ).tolist()

            fts_request["knn"] = [{
                "field": "embedding",
                "vector": query_embedding,
                "k": fts_limit,
                "filter": knn_filter
            }]
        else:
            # Text-only search
            fts_request["query"] = knn_filter

        # Call Couchbase FTS API
        couchbase_host = os.getenv('COUCHBASE_HOST', 'localhost')
        couchbase_user = os.getenv('COUCHBASE_USERNAME', 'Administrator')
        couchbase_pass = os.getenv('COUCHBASE_PASSWORD', 'password123')

        fts_url = f"http://{couchbase_host}:8094/api/index/code_vector_index/query"

        response = await http_client.post(
            fts_url,
            json=fts_request,
            auth=(couchbase_user, couchbase_pass)
        )

        if response.status_code != 200:
            logger.error(f"FTS search failed: {response.status_code} - {response.text}")
            raise HTTPException(status_code=502, detail=f"Vector search failed: {response.status_code}")

        fts_results = response.json()
        hits = fts_results.get('hits', [])

        if not hits:
            return SearchResponse(
                results=[],
                total=0,
                query=request.query,
                text_query=request.text_query,
                search_mode=search_mode,
                filters={"repo": request.repo_filter, "file_path": request.file_path_pattern, "doc_type": request.doc_type}
            )

        # Extract doc IDs and scores
        doc_ids = [hit['id'] for hit in hits]
        scores_by_id = {hit['id']: hit.get('score', 0.0) for hit in hits}

        # Fetch full documents via N1QL with filtering
        from couchbase.options import QueryOptions

        where_clauses = ["META().id IN $doc_ids", "type = $doc_type"]
        query_params = {"doc_ids": doc_ids, "doc_type": request.doc_type}

        if request.repo_filter:
            where_clauses.append("repo_id = $repo_id")
            query_params["repo_id"] = request.repo_filter

        if request.file_path_pattern:
            where_clauses.append("file_path LIKE $file_path_pattern")
            sql_pattern = request.file_path_pattern.replace('*', '%').replace('?', '_')
            query_params["file_path_pattern"] = sql_pattern

        where_clause = " AND ".join(where_clauses)

        n1ql = f"""
            SELECT META().id, repo_id, file_path, content, `language`,
                   start_line, end_line, type
            FROM `{tenant_id}`
            WHERE {where_clause}
        """

        result = db.cluster.query(n1ql, QueryOptions(named_parameters=query_params))

        # Build results with FTS scores
        results = []
        for row in result:
            doc_id = row['id']
            if row.get('type') != request.doc_type:
                continue

            results.append(SearchResultItem(
                content=row.get("content", row.get("code_text", "")),
                repo_id=row.get('repo_id', ''),
                file_path=row.get('file_path', ''),
                language=row.get('language', ''),
                score=scores_by_id.get(doc_id, 0.0),
                start_line=row.get('start_line'),
                end_line=row.get('end_line'),
                doc_type=row.get('type', request.doc_type)
            ))

        # Sort by score descending and limit
        results.sort(key=lambda x: x.score, reverse=True)
        results = results[:request.limit]

        logger.info(f"Search returned {len(results)} results (from {len(hits)} FTS hits)")

        return SearchResponse(
            results=results,
            total=len(results),
            query=request.query,
            text_query=request.text_query,
            search_mode=search_mode,
            filters={"repo": request.repo_filter, "file_path": request.file_path_pattern, "doc_type": request.doc_type}
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search request failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


class RepoInfo(BaseModel):
    """Repository information with document count"""

    repo_id: str = Field(description="Repository identifier (format: owner/repo)")
    doc_count: int = Field(description="Number of indexed documents")


class ReposResponse(BaseModel):
    """Response model for repos endpoint"""

    repos: List[RepoInfo] = Field(description="List of repositories sorted by document count")
    total_repos: int = Field(description="Total number of repositories")
    total_docs: int = Field(description="Total number of documents across all repos")


@router.get("/repos", response_model=ReposResponse)
async def list_repos(
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db),
    tenant_id: Optional[str] = None
):
    """
    List all indexed repositories with document counts.

    Returns repositories sorted by document count (descending) to help
    MCP clients understand the codebase coverage and make better queries.

    Requires authentication via JWT token in Authorization header.

    Example:
        ```bash
        curl -X GET http://localhost:8000/api/rag/repos \\
          -H "Authorization: Bearer YOUR_JWT_TOKEN"
        ```

    Returns:
        ReposResponse with list of repos and their document counts
    """
    try:
        tenant = tenant_id or current_user.get("tenant_id", "code_kosha")

        logger.info(f"Repos list request from user={current_user.get('username')} tenant={tenant}")

        # Query to get repo_id with document counts, sorted by count descending
        n1ql = f"""
            SELECT repo_id, COUNT(*) as doc_count
            FROM `{tenant}`
            WHERE repo_id IS NOT MISSING AND type = 'code_chunk'
            GROUP BY repo_id
            ORDER BY doc_count DESC
        """

        result = db.cluster.query(n1ql)

        repos = []
        total_docs = 0
        for row in result:
            repo_info = RepoInfo(
                repo_id=row['repo_id'],
                doc_count=row['doc_count']
            )
            repos.append(repo_info)
            total_docs += row['doc_count']

        logger.info(f"Found {len(repos)} repos with {total_docs} total documents")

        return ReposResponse(
            repos=repos,
            total_repos=len(repos),
            total_docs=total_docs
        )

    except Exception as e:
        logger.error(f"Repos list request failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list repos: {str(e)}")


# Singleton instance using shared CodeFetcher from lib/code-fetcher
_code_fetcher: Optional[CodeFetcher] = None


def get_code_fetcher() -> CodeFetcher:
    """Get or create CodeFetcher singleton with REPOS_PATH from environment."""
    global _code_fetcher
    if _code_fetcher is None:
        # In Docker: /repos, locally: REPOS_PATH env or default
        repos_path = os.getenv("REPOS_PATH", "/repos")
        _code_fetcher = CodeFetcher(repos_path)
    return _code_fetcher


class CodeFetchRequest(BaseModel):
    """Request model for code fetch endpoint"""

    repo_id: str = Field(description="Repository identifier (format: owner/repo)")
    file_path: str = Field(description="File path relative to repo root")
    start_line: Optional[int] = Field(default=None, ge=1, description="Start line (1-indexed). Omit for whole file.")
    end_line: Optional[int] = Field(default=None, ge=1, description="End line (1-indexed). Omit for whole file.")


class CodeFetchResponse(BaseModel):
    """Response model for code fetch endpoint"""

    code: str = Field(description="Fetched code content")
    repo_id: str = Field(description="Repository identifier")
    file_path: str = Field(description="File path")
    start_line: int = Field(description="Actual start line returned")
    end_line: int = Field(description="Actual end line returned")
    total_lines: int = Field(description="Total lines in file")
    truncated: bool = Field(default=False, description="Whether content was truncated")


@router.post("/file", response_model=CodeFetchResponse)
async def fetch_code(
    request: CodeFetchRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Fetch actual code from repository using line references.

    Complements V3 normalized chunks which store only summaries and line refs.
    When start_line/end_line omitted, returns entire file.

    Examples:
        Fetch function (lines 45-89):
        ```json
        {"repo_id": "kbhalerao/labcore", "file_path": "associates/models.py", "start_line": 45, "end_line": 89}
        ```

        Fetch entire file:
        ```json
        {"repo_id": "kbhalerao/labcore", "file_path": "associates/models.py"}
        ```
    """
    fetcher = get_code_fetcher()

    content = fetcher.get_file_content(request.repo_id, request.file_path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File not found: {request.repo_id}/{request.file_path}")

    lines = content.split('\n')
    total_lines = len(lines)

    # Determine line range
    start = request.start_line or 1
    end = request.end_line or total_lines

    # Clamp to valid range
    start = max(1, min(start, total_lines))
    end = max(start, min(end, total_lines))

    # Extract lines
    code = '\n'.join(lines[start - 1:end])

    # Truncate if too large (>100KB)
    max_size = 100_000
    truncated = len(code) > max_size
    if truncated:
        code = code[:max_size] + f"\n\n... [truncated, {len(code) - max_size} chars omitted]"

    logger.info(f"Code fetch: {request.repo_id}/{request.file_path} lines {start}-{end}/{total_lines} ({len(code)} chars)")

    return CodeFetchResponse(
        code=code,
        repo_id=request.repo_id,
        file_path=request.file_path,
        start_line=start,
        end_line=end,
        total_lines=total_lines,
        truncated=truncated
    )


@router.get("/health")
async def chat_health():
    """Health check for chat service"""
    return {
        "status": "healthy",
        "service": "chat",
        "ollama_host": settings.ollama_host
    }


