"""
FastAPI routes for RAG API (V4)

Exposes shared RAG tools as REST endpoints for both MCP and LLM modes.
"""

import os
from typing import Optional, List, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from loguru import logger

from app.dependencies import get_current_user, get_db
from app.database.couchbase_client import CouchbaseClient
from app.config import settings

# Import shared RAG tools
from app.rag.models import (
    RepoInfo,
    FileInfo,
    StructureInfo,
    SearchResult,
    FileContent,
    SearchLevel,
)
from app.rag import tools as rag_tools
from app.rag import graph_tools

# For LLM mode (ask_codebase) - legacy
from app.chat.pydantic_rag_agent import (
    CodeSmritiRAGAgent,
    ConversationMessage,
    get_embedding_model,
)

# New unified pipeline
from app.rag.pipeline import RAGPipeline, PipelineResult
from app.rag.intent import Persona, QueryIntent


router = APIRouter(prefix="/rag", tags=["CodeSmriti RAG V4"])


# =============================================================================
# Request/Response Models
# =============================================================================

class SearchRequest(BaseModel):
    """Request model for search_code endpoint"""
    query: str = Field(description="Search query (natural language or code)")
    level: Literal["symbol", "file", "module", "repo", "doc"] = Field(
        default="file",
        description="Search granularity: symbol, file, module, repo, or doc"
    )
    repo_filter: Optional[str] = Field(
        default=None,
        description="Filter by repository (format: owner/repo)"
    )
    limit: int = Field(default=5, ge=1, le=20, description="Maximum results")
    preview: bool = Field(
        default=False,
        description="If true, return only metadata without full content (for peek/preview)"
    )


class SearchResponse(BaseModel):
    """Response model for search_code endpoint"""
    results: List[SearchResult] = Field(description="Search results")
    total: int = Field(description="Number of results")
    level: str = Field(description="Search level used")


class StructureRequest(BaseModel):
    """Request model for explore_structure endpoint"""
    repo_id: str = Field(description="Repository identifier (owner/repo)")
    path: str = Field(default="", description="Path within repo (empty for root)")
    pattern: Optional[str] = Field(default=None, description="Glob pattern to filter files")
    include_summaries: bool = Field(default=False, description="Include module summaries")


class FileRequest(BaseModel):
    """Request model for get_file endpoint"""
    repo_id: str = Field(description="Repository identifier (owner/repo)")
    file_path: str = Field(description="File path relative to repo root")
    start_line: Optional[int] = Field(default=None, ge=1, description="Start line (1-indexed)")
    end_line: Optional[int] = Field(default=None, ge=1, description="End line (1-indexed)")


class AskRequest(BaseModel):
    """Request model for ask_codebase endpoint (LLM mode)"""
    query: str = Field(description="Question about the codebase", min_length=1, max_length=2000)
    level: Literal["symbol", "file", "module", "repo"] = Field(
        default="file",
        description="Search granularity for context retrieval"
    )
    conversation_history: Optional[List[dict]] = Field(
        default=None,
        description="Previous conversation messages"
    )
    stream: bool = Field(default=False, description="Enable streaming response")


class AskResponse(BaseModel):
    """Response model for ask_codebase endpoint"""
    answer: str = Field(description="Generated answer in markdown")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")


# =============================================================================
# Unified Pipeline Request/Response Models
# =============================================================================

class UnifiedAskRequest(BaseModel):
    """Request model for unified /ask endpoint."""
    query: str = Field(description="Question about the codebase or capabilities", min_length=1, max_length=2000)
    persona: Literal["developer", "sales"] = Field(
        default="developer",
        description="Persona: developer (code questions) or sales (capability/proposal questions)"
    )
    conversation_history: Optional[List[dict]] = Field(
        default=None,
        description="Previous conversation turns: [{'role': 'user'|'assistant', 'content': '...'}]"
    )


class UnifiedAskResponse(BaseModel):
    """Response model for unified /ask endpoint."""
    answer: str = Field(description="Generated answer in markdown")
    intent: str = Field(description="Classified intent type")
    direction: str = Field(description="Search direction used (broad/narrow/specific)")
    sources: List[str] = Field(default_factory=list, description="Source files/repos cited")
    levels_searched: List[str] = Field(default_factory=list, description="Search levels tried")
    adequate_context: bool = Field(default=True, description="Whether retrieval found adequate context")
    gaps: List[str] = Field(default_factory=list, description="Identified gaps in the answer")
    metadata: dict = Field(default_factory=dict, description="Additional metadata")


class ReposResponse(BaseModel):
    """Response model for list_repos endpoint"""
    repos: List[RepoInfo] = Field(description="List of repositories")
    total_repos: int = Field(description="Total number of repos")
    total_docs: int = Field(description="Total documents across all repos")


# Graph-related models
class AffectedTestsRequest(BaseModel):
    """Request model for affected_tests endpoint"""
    changed_files: List[str] = Field(
        description="List of changed file paths (e.g., ['common/models/__init__.py'])"
    )
    cluster_id: str = Field(
        description="Mother repo ID (e.g., 'kbhalerao/labcore')"
    )


class CriticalityRequest(BaseModel):
    """Request model for get_criticality endpoint"""
    module: str = Field(description="Module name (e.g., 'common.models')")
    cluster_id: str = Field(
        description="Mother repo ID (e.g., 'kbhalerao/labcore')"
    )


class GraphInfoRequest(BaseModel):
    """Request model for graph_info endpoint"""
    cluster_id: str = Field(
        description="Mother repo ID (e.g., 'kbhalerao/labcore')"
    )


# =============================================================================
# Endpoints
# =============================================================================

@router.get("/repos", response_model=ReposResponse)
async def list_repos_endpoint(
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    List all indexed repositories.

    Returns repositories sorted by document count to help understand
    codebase coverage.
    """
    tenant_id = current_user.get("tenant_id", "code_kosha")

    repos = await rag_tools.list_repos(db, tenant_id)

    total_docs = sum(r.doc_count for r in repos)

    return ReposResponse(
        repos=repos,
        total_repos=len(repos),
        total_docs=total_docs
    )


@router.post("/structure", response_model=StructureInfo)
async def explore_structure_endpoint(
    request: StructureRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Explore repository directory structure.

    Lists directories and files at the specified path. Useful for
    understanding project layout before searching.
    """
    tenant_id = current_user.get("tenant_id", "code_kosha")
    repos_path = os.getenv("REPOS_PATH", "/repos")

    return await rag_tools.explore_structure(
        db=db,
        repos_path=repos_path,
        repo_id=request.repo_id,
        path=request.path,
        pattern=request.pattern,
        include_summaries=request.include_summaries,
        tenant_id=tenant_id
    )


@router.post("/search", response_model=SearchResponse)
async def search_code_endpoint(
    request: SearchRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Semantic search across indexed documents.

    Search at different granularities:
    - **symbol**: Find specific functions/classes
    - **file**: Find relevant files (default)
    - **module**: Find relevant folders/areas
    - **repo**: High-level repository understanding
    - **doc**: Find documentation files (RST, MD)

    Use preview=true to get metadata-only results for initial exploration.
    """
    tenant_id = current_user.get("tenant_id", "code_kosha")
    embedding_model = get_embedding_model(settings.embedding_model_name)

    level = SearchLevel(request.level)

    results = await rag_tools.search_code(
        db=db,
        embedding_model=embedding_model,
        query=request.query,
        level=level,
        repo_filter=request.repo_filter,
        limit=request.limit,
        tenant_id=tenant_id,
        preview=request.preview
    )

    return SearchResponse(
        results=results,
        total=len(results),
        level=request.level
    )


@router.post("/file", response_model=FileContent)
async def get_file_endpoint(
    request: FileRequest,
    current_user: dict = Depends(get_current_user)
):
    """
    Retrieve actual source code from repository.

    Use this after search to get the actual implementation.
    Supports fetching specific line ranges.
    """
    repos_path = os.getenv("REPOS_PATH", "/repos")

    result = await rag_tools.get_file(
        repos_path=repos_path,
        repo_id=request.repo_id,
        file_path=request.file_path,
        start_line=request.start_line,
        end_line=request.end_line
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"File not found: {request.repo_id}/{request.file_path}"
        )

    return result


@router.post("/", response_model=AskResponse)
async def ask_codebase_endpoint(
    request: AskRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Ask questions about the codebase (LLM mode).

    This endpoint uses a local LLM to search and synthesize an answer.
    For MCP mode (Claude Code), use the individual tools instead.
    """
    try:
        tenant_id = current_user.get("tenant_id", "code_kosha")

        logger.info(f"ask_codebase: user={current_user.get('username')} level={request.level} query='{request.query[:50]}'")

        # Parse conversation history
        conversation_history = []
        if request.conversation_history:
            conversation_history = [
                ConversationMessage(role=msg["role"], content=msg["content"])
                for msg in request.conversation_history
            ]

        # Initialize agent
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
                try:
                    async for chunk in agent.chat_stream(request.query):
                        yield chunk
                except Exception as e:
                    logger.error(f"Streaming error: {e}", exc_info=True)
                    yield f"\n\n[Error: {str(e)}]"

            return StreamingResponse(stream_generator(), media_type="text/plain")

        # Non-streaming response
        answer = await agent.chat(request.query)

        return AskResponse(
            answer=answer,
            metadata={
                "tenant_id": tenant_id,
                "level": request.level,
                "conversation_length": len(agent.conversation_history)
            }
        )

    except Exception as e:
        logger.error(f"ask_codebase failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@router.get("/health")
async def health_check():
    """Health check for RAG service"""
    return {
        "status": "healthy",
        "service": "rag-v4",
        "ollama_host": settings.ollama_host
    }


# =============================================================================
# Unified Pipeline Endpoints
# =============================================================================

@router.post("/ask", response_model=UnifiedAskResponse)
async def unified_ask_endpoint(
    request: UnifiedAskRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Unified RAG endpoint with intent classification and progressive retrieval.

    This is the recommended endpoint for both code questions (developer persona)
    and capability/proposal questions (sales persona).

    Features:
    - Intent classification via Qwen3 tool calling
    - Query expansion for better retrieval
    - Progressive drilldown when initial results are inadequate
    - Intent-specific synthesis prompts
    - Conversation history support for follow-up questions
    """
    try:
        tenant_id = current_user.get("tenant_id", "code_kosha")
        persona = Persona(request.persona)

        logger.info(
            f"unified_ask: user={current_user.get('username')} "
            f"persona={persona.value} query='{request.query[:50]}...'"
        )

        # Initialize pipeline
        pipeline = RAGPipeline(
            db=db,
            tenant_id=tenant_id,
            lm_studio_url=settings.ollama_host,
            llm_model=settings.llm_model_name,
            embedding_model_name=settings.embedding_model_name,
        )

        # Run pipeline
        result = await pipeline.run(
            query=request.query,
            persona=persona,
            conversation_history=request.conversation_history,
        )

        return UnifiedAskResponse(
            answer=result.answer,
            intent=result.intent.value,
            direction=result.direction,
            sources=result.sources,
            levels_searched=result.levels_searched,
            adequate_context=result.adequate_context,
            gaps=result.gaps,
            metadata={
                "tenant_id": tenant_id,
                "persona": persona.value,
                "entities": result.entities,
                **result.metadata,
            }
        )

    except Exception as e:
        logger.error(f"unified_ask failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Query failed: {str(e)}")


@router.post("/ask/code", response_model=UnifiedAskResponse)
async def ask_code_endpoint(
    request: UnifiedAskRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Developer-focused endpoint for code questions.

    Convenience wrapper that forces developer persona.
    Use this for: code explanation, architecture, impact analysis, specific lookups.
    """
    request.persona = "developer"
    return await unified_ask_endpoint(request, current_user, db)


@router.post("/ask/proposal", response_model=UnifiedAskResponse)
async def ask_proposal_endpoint(
    request: UnifiedAskRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Sales-focused endpoint for capability and proposal questions.

    Convenience wrapper that forces sales persona.
    Use this for: capability checks, proposal drafts, experience summaries.
    """
    request.persona = "sales"
    return await unified_ask_endpoint(request, current_user, db)


# =============================================================================
# Graph Endpoints
# =============================================================================

@router.post("/graph/affected-tests", response_model=graph_tools.AffectedTestsResult)
async def affected_tests_endpoint(
    request: AffectedTestsRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Find tests affected by file changes.

    Given a list of changed files, uses the dependency graph to find
    all modules that transitively depend on those files, then filters
    to test modules.

    Use this to determine the minimum test suite needed after a change.
    """
    tenant_id = current_user.get("tenant_id", "code_kosha")

    return await graph_tools.find_affected_tests(
        db=db,
        changed_files=request.changed_files,
        cluster_id=request.cluster_id,
        tenant_id=tenant_id
    )


@router.post("/graph/criticality", response_model=graph_tools.CriticalityResult)
async def criticality_endpoint(
    request: CriticalityRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Get criticality info for a module.

    Returns PageRank-based criticality score, percentile ranking,
    and list of direct dependents.

    Use this to understand the impact of changing a specific module.
    """
    tenant_id = current_user.get("tenant_id", "code_kosha")

    result = await graph_tools.get_criticality(
        db=db,
        module=request.module,
        cluster_id=request.cluster_id,
        tenant_id=tenant_id
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Module '{request.module}' not found in graph '{request.cluster_id}'"
        )

    return result


@router.post("/graph/info", response_model=graph_tools.GraphInfo)
async def graph_info_endpoint(
    request: GraphInfoRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Get summary info about a dependency graph.

    Returns node/edge counts, cross-repo edges, and list of repos
    in the cluster.
    """
    tenant_id = current_user.get("tenant_id", "code_kosha")

    result = await graph_tools.get_graph_info(
        db=db,
        cluster_id=request.cluster_id,
        tenant_id=tenant_id
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Graph not found: {request.cluster_id}"
        )

    return result


@router.get("/graph/clusters")
async def list_clusters_endpoint(
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    List all available dependency graph clusters.
    """
    tenant_id = current_user.get("tenant_id", "code_kosha")

    clusters = await graph_tools.list_clusters(db, tenant_id)

    return {"clusters": clusters}


# =============================================================================
# AgSci Customer-Facing RAG
# =============================================================================

class AgSciRequest(BaseModel):
    """Request for AgSci customer-facing queries."""
    query: str = Field(description="Customer question about AgSci capabilities")
    limit: int = Field(default=5, ge=1, le=10, description="Max results to consider")


class AgSciResponse(BaseModel):
    """Response from AgSci RAG."""
    answer: str = Field(description="Synthesized answer")
    sources: List[str] = Field(description="Source documents used")


@router.post("/agsci", response_model=AgSciResponse)
async def ask_agsci_endpoint(
    request: AgSciRequest,
    current_user: dict = Depends(get_current_user),
    db: CouchbaseClient = Depends(get_db)
):
    """
    Customer-facing RAG for AgSci capabilities and documentation.

    Searches BDR briefs and documentation to help customers understand
    what AgSci can build for them. Returns business-focused answers,
    not code.

    Use this for:
    - Prospect qualification
    - Capability matching
    - Documentation questions
    """
    import httpx

    tenant_id = current_user.get("tenant_id", "code_kosha")
    embedding_model = get_embedding_model(settings.embedding_model_name)

    # LM Studio endpoint
    lm_studio_url = os.getenv("LMSTUDIO_URL", "http://macstudio.local:1234")
    lm_studio_model = os.getenv("LMSTUDIO_MODEL", "qwen/qwen3-30b-a3b-2507")

    # Step 1: Query expansion with Qwen
    expansion_prompt = f"""Given this customer question, generate 5-10 search keywords/phrases that would help find relevant capabilities and documentation.

Question: {request.query}

Output only the keywords, one per line. Include:
- Business terms the customer might use
- Technical terms that match capabilities
- Related concepts"""

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            expansion_resp = await client.post(
                f"{lm_studio_url}/v1/chat/completions",
                json={
                    "model": lm_studio_model,
                    "messages": [{"role": "user", "content": expansion_prompt}],
                    "temperature": 0.3,
                    "max_tokens": 200
                }
            )
            expansion_resp.raise_for_status()
            expanded_terms = expansion_resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"Query expansion failed: {e}, using original query")
            expanded_terms = request.query

    # Combine original query with expanded terms
    search_query = f"{request.query} {expanded_terms}"

    # Step 2: Vector search on repo_bdr and document types
    query_with_prefix = f"search_query: {search_query}"
    query_embedding = embedding_model.encode(
        query_with_prefix,
        normalize_embeddings=True
    ).tolist()

    # Search both repo_bdr and document types
    contexts = []
    sources = []

    # KNN search with filter INSIDE knn object (proper pre-filtering)
    # Search repo_bdr and document types separately to get best of each
    couchbase_host = os.getenv('COUCHBASE_HOST', 'localhost')
    fts_url = f"http://{couchbase_host}:8094/api/index/code_vector_index/query"

    for doc_type in ["repo_bdr", "document"]:
        fts_request = {
            "knn": [{
                "field": "embedding",
                "vector": query_embedding,
                "k": request.limit,
                "filter": {"term": doc_type, "field": "type"}  # Pre-filter inside knn
            }],
            "fields": ["content", "repo_id", "file_path", "type"],
            "size": request.limit
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    fts_url,
                    auth=(os.getenv("COUCHBASE_USER", "Administrator"), os.getenv("COUCHBASE_PASSWORD", "password")),
                    json=fts_request
                )
                resp.raise_for_status()
                hits = resp.json().get("hits", [])

                for hit in hits:
                    fields = hit.get("fields", {})
                    content = fields.get("content", "")
                    repo_id = fields.get("repo_id", "")
                    file_path = fields.get("file_path", "")

                    if content:
                        contexts.append(content[:2000])
                        if doc_type == "repo_bdr":
                            sources.append(f"[BDR] {repo_id}")
                        else:
                            sources.append(f"[Doc] {repo_id}/{file_path}")

        except Exception as e:
            logger.warning(f"Search failed for {doc_type}: {e}")

    if not contexts:
        return AgSciResponse(
            answer="I couldn't find relevant information about that. Could you rephrase your question or ask about specific capabilities?",
            sources=[]
        )

    # Step 3: Synthesize answer
    context_text = "\n\n---\n\n".join(contexts[:8])  # Limit total context

    synthesis_prompt = f"""You are helping a customer understand AgSci and its offerings.
Based on the following context from our documentation and capability briefs, answer the customer's question.

Be helpful, specific, and business-focused. If the context doesn't fully answer the question, say so.
Do not make up capabilities - only reference what's in the context.

## Context:
{context_text}

## Customer Question:
{request.query}

## Answer:"""

    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            synth_resp = await client.post(
                f"{lm_studio_url}/v1/chat/completions",
                json={
                    "model": lm_studio_model,
                    "messages": [{"role": "user", "content": synthesis_prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1500
                }
            )
            synth_resp.raise_for_status()
            answer = synth_resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return AgSciResponse(
                answer="I encountered an error processing your question. Please try again.",
                sources=sources
            )

    return AgSciResponse(answer=answer, sources=list(set(sources)))
