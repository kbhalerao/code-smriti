"""Intent classification for query routing and guardrails"""
from typing import Optional, List
from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel
from loguru import logger


class IntentClassification(BaseModel):
    """Structured intent classification result"""

    can_answer_from_code: bool = Field(
        description="Whether this query can be answered by searching the indexed codebase"
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence level in the classification (0.0 to 1.0)"
    )

    reasoning: str = Field(
        description="Brief explanation of why this query can/cannot be answered from code"
    )

    query_type: str = Field(
        description="Type of query: code_search, implementation_example, api_usage, debugging, architecture, general_knowledge, off_topic"
    )

    suggested_repos: List[str] = Field(
        default_factory=list,
        description="Suggested repositories to search (if mentioned in query)"
    )

    suggested_languages: List[str] = Field(
        default_factory=list,
        description="Suggested programming languages to filter (if mentioned or implied)"
    )

    max_results: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Recommended number of search results to retrieve"
    )


class IntentClassifier:
    """Classifies user queries to determine if they can be answered from codebase"""

    def __init__(self, ollama_host: str = "http://localhost:11434"):
        """
        Initialize the intent classifier.

        Args:
            ollama_host: Ollama API endpoint
        """
        self.model = OllamaModel(
            model_name="qwen2.5-coder:7b",
            base_url=ollama_host
        )

        # Create Pydantic AI agent with structured output
        self.agent = Agent(
            model=self.model,
            result_type=IntentClassification,
            system_prompt=self._get_system_prompt()
        )

    def _get_system_prompt(self) -> str:
        """Get the system prompt for intent classification"""
        return """You are an intent classifier for a code search system. Your job is to determine if a user's query can be answered by searching through indexed code repositories.

**Your task:**
1. Analyze the user's query
2. Determine if it can be answered by searching code (implementations, examples, APIs, patterns)
3. Classify the query type
4. Extract any mentioned repositories or programming languages
5. Provide confidence and reasoning

**Guidelines:**

CAN be answered from code (can_answer_from_code=True):
- "How does authentication work in the API?"
- "Show me examples of using FastAPI with Couchbase"
- "Find implementations of vector search"
- "What are the available endpoints in the auth module?"
- "How is error handling done in the ingestion worker?"

CANNOT be answered from code (can_answer_from_code=False):
- "What's the weather today?"
- "Explain quantum physics"
- "Write a poem about programming"
- "What are the latest Python releases?"
- "Tell me a joke"

**Query Types:**
- code_search: Looking for specific code patterns or functions
- implementation_example: Wants to see how something is implemented
- api_usage: How to use an API or library
- debugging: Troubleshooting or understanding errors
- architecture: Understanding system design and structure
- general_knowledge: Generic programming questions (may or may not need code)
- off_topic: Completely unrelated to code

**Extract context:**
- If query mentions "repo:xyz" or "in the ABC repository" → add to suggested_repos
- If query mentions "Python", "JavaScript", "TypeScript" etc. → add to suggested_languages
- Suggest max_results based on query specificity (specific=5, broad=10-15)

Respond with structured classification data only."""

    async def classify(self, query: str, available_repos: Optional[List[str]] = None) -> IntentClassification:
        """
        Classify a user query to determine if it can be answered from the codebase.

        Args:
            query: The user's query
            available_repos: Optional list of available repositories (for context)

        Returns:
            IntentClassification with routing decision and metadata
        """
        logger.info(f"Classifying intent for query: '{query}'")

        try:
            # Add context about available repos if provided
            context = ""
            if available_repos:
                context = f"\n\nAvailable repositories: {', '.join(available_repos[:10])}"
                if len(available_repos) > 10:
                    context += f" (and {len(available_repos) - 10} more)"

            # Run the classification
            result = await self.agent.run(query + context)
            classification = result.data

            logger.info(
                f"Intent classified: can_answer={classification.can_answer_from_code}, "
                f"type={classification.query_type}, confidence={classification.confidence}"
            )

            return classification

        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            # Default to allowing the query (fail open for now)
            return IntentClassification(
                can_answer_from_code=True,
                confidence=0.5,
                reasoning=f"Classification failed, allowing query by default. Error: {str(e)}",
                query_type="general_knowledge",
                max_results=5
            )
