"""Main RAG agent with two-phase architecture: Intent → Research"""
from typing import Optional, List
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.ollama import OllamaModel
from loguru import logger

from app.database.couchbase_client import CouchbaseClient
from app.chat.intent import IntentClassifier, IntentClassification
from app.chat.tools import CodeSearchTool, ListReposTool, SearchResult


class ChatResponse(BaseModel):
    """Structured chat response"""

    answer: str = Field(description="The generated answer to the user's query")

    intent_classification: IntentClassification = Field(
        description="The intent classification that was performed"
    )

    sources: List[SearchResult] = Field(
        default_factory=list,
        description="Source code chunks used to generate the answer"
    )

    reasoning: str = Field(
        description="Brief explanation of how the answer was derived"
    )


class RAGAgent:
    """Two-phase RAG agent: Intent Classification → Code Research"""

    def __init__(
        self,
        db: CouchbaseClient,
        tenant_id: str,
        ollama_host: str = "http://localhost:11434"
    ):
        """
        Initialize the RAG agent.

        Args:
            db: Couchbase client instance
            tenant_id: Tenant identifier for multi-tenant access
            ollama_host: Ollama API endpoint
        """
        self.db = db
        self.tenant_id = tenant_id
        self.ollama_host = ollama_host

        # Phase 1: Intent classifier
        self.intent_classifier = IntentClassifier(ollama_host=ollama_host)

        # Phase 2: Research agent with tools
        self.search_tool = CodeSearchTool(db=db, tenant_id=tenant_id)
        self.repos_tool = ListReposTool(db=db, tenant_id=tenant_id)

        # Initialize the main research agent
        self.model = OllamaModel(
            model_name="qwen2.5-coder:7b",
            base_url=ollama_host
        )

        self.agent = Agent(
            model=self.model,
            result_type=str,  # Free-form answer
            system_prompt=self._get_system_prompt()
        )

        # Register tools with the agent
        self._register_tools()

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the research agent"""
        return """You are a helpful code research assistant with access to indexed code repositories.

**Your capabilities:**
- Search through code using semantic search
- Find relevant implementations, examples, and patterns
- Explain code architecture and design decisions
- Provide concrete code examples from the indexed repositories

**Guidelines:**
1. ALWAYS use the search_code tool to find relevant code before answering
2. Cite specific files and repositories in your answers
3. If you can't find relevant code, say so honestly
4. Provide code snippets when helpful
5. Explain both WHAT the code does and WHY it's designed that way

**Answer format:**
- Start with a direct answer to the question
- Provide relevant code examples with file paths
- Explain the implementation details
- Suggest related files or patterns if relevant

Be concise but thorough. Focus on the code."""

    def _register_tools(self):
        """Register Pydantic AI tools with the agent"""

        @self.agent.tool
        async def search_code(
            ctx: RunContext,
            query: str,
            limit: int = 5,
            repo: Optional[str] = None,
            language: Optional[str] = None
        ) -> List[SearchResult]:
            """Search for code using semantic search"""
            return await self.search_tool.search_code(
                query=query,
                limit=limit,
                repo=repo,
                language=language
            )

        @self.agent.tool
        async def list_repos(ctx: RunContext) -> List[str]:
            """List all available repositories"""
            return await self.repos_tool.list_repos()

    async def chat(self, query: str) -> ChatResponse:
        """
        Process a chat query using two-phase architecture.

        Phase 1: Intent Classification
        - Determine if query can be answered from code
        - Extract search parameters (repos, languages, etc.)
        - Act as guardrails

        Phase 2: RAG Research
        - Search code using configured parameters
        - Generate answer with retrieved context

        Args:
            query: User's query

        Returns:
            ChatResponse with answer and metadata
        """
        logger.info(f"Processing chat query: '{query}'")

        # ========================================================================
        # PHASE 1: Intent Classification
        # ========================================================================
        logger.info("Phase 1: Classifying intent...")

        # Get available repos for context
        available_repos = await self.repos_tool.list_repos()

        # Classify the intent
        intent = await self.intent_classifier.classify(
            query=query,
            available_repos=available_repos
        )

        logger.info(
            f"Intent: can_answer={intent.can_answer_from_code}, "
            f"type={intent.query_type}, confidence={intent.confidence:.2f}"
        )

        # Guardrail: Reject queries that can't be answered from code
        if not intent.can_answer_from_code:
            logger.warning(f"Query rejected by intent classifier: {intent.reasoning}")
            return ChatResponse(
                answer=f"I cannot answer this query using the indexed codebase.\n\n"
                       f"Reason: {intent.reasoning}\n\n"
                       f"I can only help with questions about code implementations, "
                       f"API usage, architecture, and examples from the indexed repositories.",
                intent_classification=intent,
                sources=[],
                reasoning=intent.reasoning
            )

        # Low confidence warning
        if intent.confidence < 0.6:
            logger.warning(f"Low confidence classification: {intent.confidence:.2f}")

        # ========================================================================
        # PHASE 2: RAG Research
        # ========================================================================
        logger.info("Phase 2: Performing RAG research...")

        # Configure search based on intent classification
        search_params = {
            "query": query,
            "limit": intent.max_results,
            "repo": intent.suggested_repos[0] if intent.suggested_repos else None,
            "language": intent.suggested_languages[0] if intent.suggested_languages else None
        }

        logger.info(f"Search parameters: {search_params}")

        # TODO: Actually search code (placeholder for now)
        sources = await self.search_tool.search_code(**search_params)

        # Build context for the LLM
        context = self._build_context(sources)

        # Generate answer using the research agent
        # Note: The agent will use tools if needed during generation
        result = await self.agent.run(
            f"{query}\n\nContext from codebase:\n{context}"
        )

        answer = result.data

        logger.info("Answer generated successfully")

        return ChatResponse(
            answer=answer,
            intent_classification=intent,
            sources=sources,
            reasoning=f"Found {len(sources)} relevant code chunks from "
                     f"{len(set(s.repo_id for s in sources))} repositories"
        )

    def _build_context(self, sources: List[SearchResult]) -> str:
        """Build context string from search results"""
        if not sources:
            return "No relevant code found in the indexed repositories."

        context_parts = []
        for i, source in enumerate(sources, 1):
            context_parts.append(
                f"[{i}] {source.repo_id}/{source.file_path} ({source.language})\n"
                f"```{source.language}\n{source.content}\n```\n"
            )

        return "\n".join(context_parts)
