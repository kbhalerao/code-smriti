"""
PydanticAI RAG Agent (V4)

LLM-driven RAG agent using PydanticAI for code-smriti.
Uses shared tool layer with V4 hierarchical document structure.

Features:
- Progressive disclosure via search levels (symbol, file, module, repo)
- Tool-calling architecture for flexible search
- Streaming response support
- OpenAI-compatible endpoint support (works with LM Studio)
"""

import os
from typing import List, Optional
from dataclasses import dataclass

import httpx
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from sentence_transformers import SentenceTransformer

from app.database.couchbase_client import CouchbaseClient
from app.rag.models import SearchLevel, SearchResult, FileContent
from app.rag import tools as rag_tools


# =============================================================================
# Models
# =============================================================================

class CodeChunk(BaseModel):
    """Represents a code chunk from search results."""
    content: str = Field(description="The code/summary content")
    repo_id: str = Field(description="Repository identifier")
    file_path: str = Field(description="File path in repository")
    language: str = Field(description="Programming language")
    score: float = Field(description="Relevance score (0-1)")
    start_line: Optional[int] = Field(default=None, description="Start line number")
    end_line: Optional[int] = Field(default=None, description="End line number")
    doc_type: str = Field(default="file_index", description="Document type")


class ConversationMessage(BaseModel):
    """Single message in conversation history."""
    role: str = Field(description="Role: 'user' or 'assistant'")
    content: str = Field(description="Message content")


@dataclass
class RAGContext:
    """Context passed to agent tools."""
    db: CouchbaseClient
    tenant_id: str
    repos_path: str
    ollama_host: str
    http_client: httpx.AsyncClient
    embedding_model: SentenceTransformer
    conversation_history: List[ConversationMessage]


# =============================================================================
# Singletons
# =============================================================================

_embedding_model: Optional[SentenceTransformer] = None
_http_client: Optional[httpx.AsyncClient] = None
_rag_agent: Optional[Agent] = None


def get_embedding_model(model_name: str = "nomic-ai/nomic-embed-text-v1.5") -> SentenceTransformer:
    """Get or create the embedding model (singleton)."""
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model: {model_name}")
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        _embedding_model = SentenceTransformer(model_name, trust_remote_code=True)
        logger.info(f"Embedding model loaded: {model_name}")
    return _embedding_model


def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared HTTP client (singleton)."""
    global _http_client
    if _http_client is None:
        logger.info("Creating shared httpx AsyncClient")
        _http_client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
    return _http_client


def get_rag_agent(ollama_host: str, llm_model: str) -> Agent[RAGContext, str]:
    """Get or create the shared PydanticAI agent (singleton)."""
    global _rag_agent
    if _rag_agent is None:
        logger.info(f"Creating PydanticAI agent with model: {llm_model}")
        _rag_agent = create_rag_agent(ollama_host, llm_model)
    return _rag_agent


# =============================================================================
# Agent Creation
# =============================================================================

SYSTEM_PROMPT = """You are a code research assistant for code-smriti, a knowledge base that indexes code repositories.

Your role:
1. **Understand queries** - Determine the appropriate search granularity
2. **Search strategically** - Use the right level for the question:
   - "repo" level for high-level project questions
   - "module" level for architecture/organization questions
   - "file" level for implementation questions (default)
   - "symbol" level for specific function/class questions
3. **Retrieve code** - Use get_file to fetch actual implementations when needed
4. **Generate narratives** - Create clear markdown responses with code examples

Guidelines:
- Only answer questions about code in the indexed repositories
- If a query is off-topic, politely decline
- Always cite specific files with line numbers when referencing code
- Format code blocks with proper language tags
- Be concise but thorough

Output format:
- Brief summary
- Relevant code snippets with file:line references
- Markdown formatting: headers, lists, code blocks
- Actionable insights if appropriate
"""


def create_rag_agent(ollama_host: str, llm_model: str) -> Agent[RAGContext, str]:
    """Create PydanticAI agent for RAG using OpenAI-compatible endpoint."""

    base_url = ollama_host if ollama_host.endswith("/v1") else f"{ollama_host}/v1"
    os.environ['OPENAI_BASE_URL'] = base_url
    if 'OPENAI_API_KEY' not in os.environ:
        os.environ['OPENAI_API_KEY'] = 'dummy'

    logger.info(f"Configuring PydanticAI: base_url={base_url}, model={llm_model}")

    agent = Agent(
        f'openai:{llm_model}',
        system_prompt=SYSTEM_PROMPT,
        deps_type=RAGContext,
        output_type=str,
        retries=1,
    )

    # -------------------------------------------------------------------------
    # Tool: search_code
    # -------------------------------------------------------------------------
    @agent.tool
    async def search_code(
        ctx: RunContext[RAGContext],
        query: str,
        level: str = "file",
        limit: int = 5,
        repo_filter: Optional[str] = None
    ) -> List[CodeChunk]:
        """
        Search for code across indexed repositories using semantic vector search.

        Args:
            query: Natural language or code search query
            level: Search granularity - "symbol", "file", "module", or "repo"
                   Use "symbol" for specific functions/classes
                   Use "file" for relevant files (default)
                   Use "module" for folders/areas of code
                   Use "repo" for high-level understanding
            limit: Maximum number of results (default: 5, max: 10)
            repo_filter: Optional repository filter (format: owner/repo)

        Returns:
            List of relevant code chunks with metadata
        """
        logger.info(f"Tool: search_code(query='{query[:50]}', level={level}, limit={limit})")

        try:
            search_level = SearchLevel(level)
        except ValueError:
            search_level = SearchLevel.FILE

        results = await rag_tools.search_code(
            db=ctx.deps.db,
            embedding_model=ctx.deps.embedding_model,
            query=query,
            level=search_level,
            repo_filter=repo_filter,
            limit=min(limit, 10),
            tenant_id=ctx.deps.tenant_id
        )

        # Convert to CodeChunk for LLM consumption
        chunks = []
        for r in results:
            chunks.append(CodeChunk(
                content=r.content,
                repo_id=r.repo_id,
                file_path=r.file_path or "",
                language="",  # V4 doesn't store language at search result level
                score=r.score,
                start_line=r.start_line,
                end_line=r.end_line,
                doc_type=r.doc_type
            ))

        logger.info(f"search_code found {len(chunks)} results")
        return chunks

    # -------------------------------------------------------------------------
    # Tool: get_file
    # -------------------------------------------------------------------------
    @agent.tool
    async def get_file(
        ctx: RunContext[RAGContext],
        repo_id: str,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None
    ) -> str:
        """
        Retrieve actual source code from a repository file.

        Args:
            repo_id: Repository identifier (e.g., "kbhalerao/labcore")
            file_path: Path to file relative to repo root
            start_line: Optional start line (1-indexed). Omit for entire file.
            end_line: Optional end line (1-indexed). Omit for entire file.

        Returns:
            The file content as a string
        """
        logger.info(f"Tool: get_file({repo_id}/{file_path}, lines {start_line}-{end_line})")

        result = await rag_tools.get_file(
            repos_path=ctx.deps.repos_path,
            repo_id=repo_id,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line
        )

        if result is None:
            return f"File not found: {repo_id}/{file_path}"

        header = f"# {repo_id}/{file_path}\n"
        header += f"# Lines {result.start_line}-{result.end_line} of {result.total_lines}\n\n"
        return header + result.code

    # -------------------------------------------------------------------------
    # Tool: list_repos
    # -------------------------------------------------------------------------
    @agent.tool
    async def list_repos(
        ctx: RunContext[RAGContext],
        filter_text: Optional[str] = None
    ) -> List[str]:
        """
        List all repositories available in the indexed codebase.

        Args:
            filter_text: Optional filter to search for repos containing this text

        Returns:
            List of repository identifiers (format: owner/repo)
        """
        logger.info(f"Tool: list_repos(filter={filter_text})")

        repos = await rag_tools.list_repos(ctx.deps.db, ctx.deps.tenant_id)

        # Apply filter if specified
        if filter_text:
            filter_lower = filter_text.lower()
            repos = [r for r in repos if filter_lower in r.repo_id.lower()]

        return [f"{r.repo_id} ({r.doc_count} docs)" for r in repos]

    # -------------------------------------------------------------------------
    # Tool: explore_structure
    # -------------------------------------------------------------------------
    @agent.tool
    async def explore_structure(
        ctx: RunContext[RAGContext],
        repo_id: str,
        path: str = "",
        pattern: Optional[str] = None,
        include_summaries: bool = False
    ) -> str:
        """
        Explore repository directory structure.

        Use this to navigate and understand project layout. Similar to 'ls'.
        Call this before searching to understand where code is organized.

        Args:
            repo_id: Repository identifier (e.g., "kbhalerao/labcore")
            path: Path within repo (empty string for root, e.g., "src/", "tests/")
            pattern: Optional glob pattern to filter files (e.g., "*.py", "test_*")
            include_summaries: Include module summary if available

        Returns:
            Directory listing with subdirectories, files, and key files
        """
        logger.info(f"Tool: explore_structure({repo_id}/{path})")

        result = await rag_tools.explore_structure(
            db=ctx.deps.db,
            repos_path=ctx.deps.repos_path,
            repo_id=repo_id,
            path=path,
            pattern=pattern,
            include_summaries=include_summaries,
            tenant_id=ctx.deps.tenant_id
        )

        # Format as readable string for LLM
        output = [f"Directory: {repo_id}/{path or '(root)'}\n"]

        if result.key_files:
            output.append("Key files:")
            for key_type, key_path in result.key_files.items():
                output.append(f"  - {key_type}: {key_path}")
            output.append("")

        if result.directories:
            output.append("Directories:")
            for d in result.directories:
                output.append(f"  - {d}")
            output.append("")

        if result.files:
            output.append("Files:")
            for f in result.files:
                indexed = " [indexed]" if f.has_summary else ""
                lang = f" ({f.language})" if f.language else ""
                output.append(f"  - {f.name}{lang} - {f.line_count} lines{indexed}")
            output.append("")

        if result.summary:
            output.append("Module Summary:")
            output.append(result.summary)

        if not result.directories and not result.files:
            output.append("(empty or not found)")

        return "\n".join(output)

    return agent


# =============================================================================
# Agent Wrapper
# =============================================================================

class CodeSmritiRAGAgent:
    """
    High-level wrapper for code-smriti RAG agent.

    Uses shared singleton resources (agent, HTTP client, embedding model)
    to avoid creating new connections per request.
    """

    def __init__(
        self,
        db: CouchbaseClient,
        tenant_id: str,
        ollama_host: str = "http://localhost:11434",
        llm_model: str = "deepseek-coder:6.7b",
        embedding_model_name: str = "nomic-ai/nomic-embed-text-v1.5",
        conversation_history: Optional[List[ConversationMessage]] = None
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.ollama_host = ollama_host
        self.repos_path = os.getenv("REPOS_PATH", "/repos")
        self.conversation_history = conversation_history or []

        # Shared singletons
        self.http_client = get_http_client()
        self.embedding_model = get_embedding_model(embedding_model_name)
        self.agent = get_rag_agent(ollama_host, llm_model)

    async def chat(self, query: str) -> str:
        """Process a chat query with RAG."""
        logger.info(f"Processing query: '{query[:100]}'")

        if not self._is_valid_query(query):
            return (
                "I can only help with questions about code, APIs, implementations, "
                "and technical documentation in the indexed repositories."
            )

        ctx = RAGContext(
            db=self.db,
            tenant_id=self.tenant_id,
            repos_path=self.repos_path,
            ollama_host=self.ollama_host,
            http_client=self.http_client,
            embedding_model=self.embedding_model,
            conversation_history=self.conversation_history
        )

        prompt = self._build_prompt_with_history(query)
        result = await self.agent.run(prompt, deps=ctx)

        # Update history
        self.conversation_history.append(ConversationMessage(role="user", content=query))
        self.conversation_history.append(ConversationMessage(role="assistant", content=result.output))

        # Keep last 6 messages
        if len(self.conversation_history) > 6:
            self.conversation_history = self.conversation_history[-6:]

        return result.output

    async def chat_stream(self, query: str):
        """Process a chat query with streaming response."""
        logger.info(f"Processing query (streaming): '{query[:100]}'")

        if not self._is_valid_query(query):
            yield (
                "I can only help with questions about code, APIs, implementations, "
                "and technical documentation in the indexed repositories."
            )
            return

        ctx = RAGContext(
            db=self.db,
            tenant_id=self.tenant_id,
            repos_path=self.repos_path,
            ollama_host=self.ollama_host,
            http_client=self.http_client,
            embedding_model=self.embedding_model,
            conversation_history=self.conversation_history
        )

        prompt = self._build_prompt_with_history(query)
        full_response = []

        async with self.agent.run_stream(prompt, deps=ctx) as result:
            async for chunk in result.stream():
                full_response.append(chunk)
                yield chunk

        # Update history
        self.conversation_history.append(ConversationMessage(role="user", content=query))
        self.conversation_history.append(ConversationMessage(role="assistant", content="".join(full_response)))

        if len(self.conversation_history) > 6:
            self.conversation_history = self.conversation_history[-6:]

    def _is_valid_query(self, query: str) -> bool:
        """Check if query is code-related."""
        query_lower = query.lower()

        off_topic = ['weather', 'joke', 'recipe', 'movie', 'sports', 'news']
        if any(word in query_lower for word in off_topic):
            return False

        code_keywords = [
            'code', 'function', 'class', 'api', 'implement', 'how does',
            'show me', 'example', 'authentication', 'database', 'endpoint',
            'route', 'handler', 'service', 'component', 'module', 'package',
            'bug', 'error', 'fix', 'refactor', 'optimize', 'test'
        ]
        if any(keyword in query_lower for keyword in code_keywords):
            return True

        return len(query.split()) >= 3

    def _build_prompt_with_history(self, query: str) -> str:
        """Build prompt with conversation history."""
        if not self.conversation_history:
            return query

        history_text = "\n\n".join([
            f"{'User' if msg.role == 'user' else 'Assistant'}: {msg.content}"
            for msg in self.conversation_history[-4:]
        ])

        return f"""Previous conversation:
{history_text}

Current question: {query}

Answer the current question using the conversation history for context."""
