"""
Production-quality RAG agent using PydanticAI for code-smriti.

Features:
- Intent classification with conversation context
- Vector search using Couchbase FTS + kNN
- Tool-calling architecture for flexible search
- Streaming response support
- High-quality markdown narrative generation
"""

import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import httpx
from loguru import logger
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.ollama import OllamaProvider
from sentence_transformers import SentenceTransformer

from app.database.couchbase_client import CouchbaseClient


# ============================================================================
# Models and Data Structures
# ============================================================================

class CodeChunk(BaseModel):
    """Represents a code chunk from search results."""
    content: str = Field(description="The code content")
    repo_id: str = Field(description="Repository identifier")
    file_path: str = Field(description="File path in repository")
    language: str = Field(description="Programming language")
    score: float = Field(description="Relevance score (0-1)")
    start_line: Optional[int] = Field(default=None, description="Start line number")
    end_line: Optional[int] = Field(default=None, description="End line number")


class ConversationMessage(BaseModel):
    """Single message in conversation history."""
    role: str = Field(description="Role: 'user' or 'assistant'")
    content: str = Field(description="Message content")


@dataclass
class RAGContext:
    """Context passed to agent tools."""
    db: CouchbaseClient
    tenant_id: str
    ollama_host: str
    http_client: httpx.AsyncClient
    embedding_model: SentenceTransformer
    conversation_history: List[ConversationMessage]


# ============================================================================
# Global Singletons (Shared Resources)
# ============================================================================

_embedding_model: Optional[SentenceTransformer] = None
_http_client: Optional[httpx.AsyncClient] = None
_rag_agent: Optional[Agent] = None


def get_embedding_model(model_name: str = "nomic-ai/nomic-embed-text-v1.5") -> SentenceTransformer:
    """Get or create the embedding model (singleton)."""
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading sentence-transformers embedding model: {model_name}")
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        # MUST match ingestion model for vector search to work
        _embedding_model = SentenceTransformer(
            model_name,
            trust_remote_code=True
        )
        logger.info(f"✓ Embedding model loaded: {model_name}")
    return _embedding_model


def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared HTTP client (singleton)."""
    global _http_client
    if _http_client is None:
        logger.info("Creating shared httpx AsyncClient with connection pooling")
        _http_client = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20)
        )
        logger.info("✓ HTTP client created with max_connections=100")
    return _http_client


def get_rag_agent(ollama_host: str = "http://localhost:11434", llm_model: str = "deepseek-coder:6.7b") -> Agent[RAGContext, str]:
    """Get or create the shared PydanticAI agent (singleton)."""
    global _rag_agent
    if _rag_agent is None:
        logger.info(f"Creating shared PydanticAI agent with model: {llm_model}")
        _rag_agent = create_rag_agent(ollama_host, llm_model)
        logger.info("✓ PydanticAI agent created")
    return _rag_agent


async def close_shared_resources():
    """Close all shared resources (for graceful shutdown)."""
    global _http_client, _rag_agent, _embedding_model

    if _http_client:
        logger.info("Closing shared HTTP client")
        await _http_client.aclose()
        _http_client = None

    # Note: Embedding model and agent don't need explicit cleanup
    _rag_agent = None
    _embedding_model = None
    logger.info("✓ Shared resources closed")


# ============================================================================
# PydanticAI Agent with Tools
# ============================================================================

# System prompt for the agent
SYSTEM_PROMPT = """You are a code research assistant for code-smriti, a knowledge base system that indexes code repositories.

Your role:
1. **Understand user queries** - Determine if the question is about code in the indexed repositories
2. **Search strategically** - Use vector search to find relevant code chunks
3. **Generate high-quality narratives** - Create clear, well-formatted markdown responses with code examples

Guidelines:
- Only answer questions about code, architecture, implementations, APIs, and technical documentation
- If a query is off-topic (weather, jokes, general knowledge), politely decline
- Use conversation history to understand context and follow-up questions
- Always cite specific files when referencing code
- Format code blocks with proper language tags for syntax highlighting
- Be concise but thorough - focus on what's relevant to the query

Output format:
- Start with a brief summary
- Include relevant code snippets with file references
- Use markdown formatting: headers, lists, code blocks
- End with actionable insights or next steps if appropriate
"""


# Initialize the agent with Ollama model
def create_rag_agent(ollama_host: str = "http://localhost:11434", llm_model: str = "llama3.1:latest") -> Agent[RAGContext, str]:
    """Create PydanticAI agent for RAG."""

    # Use OpenAIChatModel with OllamaProvider
    # Note: Tool calling with Ollama has known issues in pydantic-ai v1.21.0
    base_url = ollama_host if ollama_host.endswith("/v1") or ollama_host.endswith("/v1/") else f"{ollama_host}/v1"

    model = OpenAIChatModel(
        model_name=llm_model,
        provider=OllamaProvider(base_url=base_url)
    )

    agent = Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        deps_type=RAGContext,
        output_type=str,
        retries=1,
    )

    # Register tools

    @agent.tool
    async def search_code(
        ctx: RunContext[RAGContext],
        query: str,
        limit: int = 5,
        repo_filter: Optional[str] = None
    ) -> List[CodeChunk]:
        """
        Search for code across indexed repositories using semantic vector search.

        Args:
            query: Natural language or code search query
            limit: Maximum number of results (default: 5, max: 10)
            repo_filter: Optional repository filter (format: owner/repo)

        Returns:
            List of relevant code chunks with metadata
        """
        logger.warning(f"🔧 TOOL EXECUTED: search_code(query='{query}', limit={limit}, repo={repo_filter})")
        logger.info(f"Tool called: search_code(query='{query}', limit={limit}, repo={repo_filter})")

        try:
            # Generate embedding for the query
            logger.debug("Generating query embedding...")
            # MUST use same prefix as ingestion: 'search_document:'
            query_with_prefix = f"search_document: {query}"
            query_embedding = ctx.deps.embedding_model.encode(query_with_prefix).tolist()

            # Build FTS search request with kNN vector search
            search_request = {
                "query": {
                    "match_none": {}  # We only want vector results
                },
                "knn": [
                    {
                        "field": "embedding",
                        "vector": query_embedding,
                        "k": min(limit, 10)  # Cap at 10
                    }
                ],
                "size": min(limit, 10),
                "fields": ["*"]
            }

            # Add repository filter if specified
            if repo_filter:
                search_request["query"] = {
                    "conjuncts": [
                        {"match": repo_filter, "field": "repo_id"}
                    ]
                }

            # Call Couchbase FTS API
            couchbase_host = os.getenv('COUCHBASE_HOST', 'localhost')
            couchbase_user = os.getenv('COUCHBASE_USERNAME', 'Administrator')
            couchbase_pass = os.getenv('COUCHBASE_PASSWORD', 'password123')

            fts_url = f"http://{couchbase_host}:8094/api/index/code_vector_index/query"

            response = await ctx.deps.http_client.post(
                fts_url,
                json=search_request,
                auth=(couchbase_user, couchbase_pass)
            )

            if response.status_code != 200:
                logger.error(f"FTS search failed: {response.status_code} - {response.text}")
                return []

            result = response.json()
            hits = result.get('hits', [])

            # Fetch full documents from Couchbase
            code_chunks = []
            for hit in hits:
                doc_id = hit.get('id')
                if not doc_id:
                    continue

                try:
                    bucket = ctx.deps.db.cluster.bucket(ctx.deps.tenant_id)
                    collection = bucket.default_collection()
                    doc_result = collection.get(doc_id)
                    doc = doc_result.content_as[dict]

                    code_chunks.append(CodeChunk(
                        content=doc.get('content', doc.get('code_text', '')),  # Unified schema with fallback
                        repo_id=doc.get('repo_id', ''),
                        file_path=doc.get('file_path', ''),
                        language=doc.get('language', ''),
                        score=hit.get('score', 0.0),
                        start_line=doc.get('start_line'),
                        end_line=doc.get('end_line')
                    ))
                except Exception as e:
                    logger.warning(f"Failed to fetch document {doc_id}: {e}")
                    continue

            logger.info(f"Found {len(code_chunks)} code chunks (scores: {[c.score for c in code_chunks[:3]]})")
            return code_chunks

        except Exception as e:
            logger.error(f"Vector search failed: {e}", exc_info=True)
            return []


    @agent.tool
    async def list_available_repos(
        ctx: RunContext[RAGContext],
        repo_filter: Optional[str] = None
    ) -> List[str]:
        """
        List all repositories available in the indexed codebase.

        Args:
            repo_filter: Optional filter to search for repos containing this text (e.g., 'labcore', 'farmworth')

        Returns:
            List of repository identifiers (format: owner/repo)
        """
        logger.info(f"Tool called: list_available_repos(repo_filter={repo_filter})")

        try:
            # Build query with optional filter
            if repo_filter:
                query = f"""
                    SELECT DISTINCT repo_id
                    FROM `{ctx.deps.tenant_id}`
                    WHERE repo_id IS NOT MISSING
                      AND LOWER(repo_id) LIKE LOWER('%{repo_filter}%')
                    ORDER BY repo_id
                """
            else:
                query = f"""
                    SELECT DISTINCT repo_id
                    FROM `{ctx.deps.tenant_id}`
                    WHERE repo_id IS NOT MISSING
                    ORDER BY repo_id
                """

            result = ctx.deps.db.cluster.query(query)
            repos = [row['repo_id'] for row in result]

            logger.info(f"Found {len(repos)} repositories")
            return repos

        except Exception as e:
            logger.error(f"List repos failed: {e}")
            return []


    return agent


# ============================================================================
# RAG Agent Wrapper
# ============================================================================

class CodeSmritiRAGAgent:
    """
    High-level wrapper for code-smriti RAG agent.

    Note: This class uses shared singleton resources (agent, HTTP client, embedding model)
    to avoid creating new connections and models per request. Only conversation state
    is per-instance.
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
        self.conversation_history = conversation_history or []

        # Use shared singletons (created once per server process)
        self.http_client = get_http_client()
        self.embedding_model = get_embedding_model(embedding_model_name)
        self.agent = get_rag_agent(ollama_host, llm_model)


    async def chat(self, query: str) -> str:
        """
        Process a chat query with RAG.

        Args:
            query: User's query

        Returns:
            Generated response as markdown
        """
        logger.info(f"Processing query: '{query}'")

        # Validate intent (simple heuristic check)
        if not self._is_valid_query(query):
            return (
                "I can only help with questions about code, APIs, implementations, "
                "and technical documentation in the indexed repositories. "
                "Your query seems to be off-topic. Please ask about code-related topics."
            )

        # Create context
        ctx = RAGContext(
            db=self.db,
            tenant_id=self.tenant_id,
            ollama_host=self.ollama_host,
            http_client=self.http_client,
            embedding_model=self.embedding_model,
            conversation_history=self.conversation_history
        )

        # Build prompt with conversation history
        prompt = self._build_prompt_with_history(query)

        # Run the agent
        result = await self.agent.run(prompt, deps=ctx)

        # Add to conversation history
        self.conversation_history.append(ConversationMessage(role="user", content=query))
        self.conversation_history.append(ConversationMessage(role="assistant", content=result.output))

        # Keep only last 6 messages (3 exchanges)
        if len(self.conversation_history) > 6:
            self.conversation_history = self.conversation_history[-6:]

        return result.output


    async def chat_stream(self, query: str):
        """
        Process a chat query with streaming response.

        Args:
            query: User's query

        Yields:
            Chunks of the response as they're generated
        """
        logger.info(f"Processing query (streaming): '{query}'")

        # Validate intent
        if not self._is_valid_query(query):
            yield (
                "I can only help with questions about code, APIs, implementations, "
                "and technical documentation in the indexed repositories. "
                "Your query seems to be off-topic. Please ask about code-related topics."
            )
            return

        # Create context
        ctx = RAGContext(
            db=self.db,
            tenant_id=self.tenant_id,
            ollama_host=self.ollama_host,
            http_client=self.http_client,
            embedding_model=self.embedding_model,
            conversation_history=self.conversation_history
        )

        # Build prompt with conversation history
        prompt = self._build_prompt_with_history(query)

        # Run the agent with streaming
        full_response = []
        async with self.agent.run_stream(prompt, deps=ctx) as result:
            async for chunk in result.stream():
                full_response.append(chunk)
                yield chunk

        # Add to conversation history
        self.conversation_history.append(ConversationMessage(role="user", content=query))
        self.conversation_history.append(ConversationMessage(role="assistant", content="".join(full_response)))

        # Keep only last 6 messages
        if len(self.conversation_history) > 6:
            self.conversation_history = self.conversation_history[-6:]


    def _is_valid_query(self, query: str) -> bool:
        """
        Simple heuristic to check if query is code-related.

        This is a lightweight gate. The agent can still refuse if needed.
        """
        query_lower = query.lower()

        # Off-topic keywords
        off_topic = ['weather', 'joke', 'recipe', 'movie', 'sports', 'news']
        if any(word in query_lower for word in off_topic):
            return False

        # Code-related keywords (positive signals)
        code_keywords = [
            'code', 'function', 'class', 'api', 'implement', 'how does',
            'show me', 'example', 'authentication', 'database', 'endpoint',
            'route', 'handler', 'service', 'component', 'module', 'package',
            'bug', 'error', 'fix', 'refactor', 'optimize', 'test'
        ]
        if any(keyword in query_lower for keyword in code_keywords):
            return True

        # Default: allow if query is reasonably long (likely a real question)
        return len(query.split()) >= 3


    def _build_prompt_with_history(self, query: str) -> str:
        """Build prompt with conversation history for context."""
        if not self.conversation_history:
            return query

        # Format conversation history
        history_text = "\n\n".join([
            f"{'User' if msg.role == 'user' else 'Assistant'}: {msg.content}"
            for msg in self.conversation_history[-4:]  # Last 2 exchanges
        ])

        return f"""Previous conversation:
{history_text}

Current question: {query}

Please answer the current question, taking into account the conversation history for context."""
