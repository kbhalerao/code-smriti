"""
Intent Classification for RAG Pipeline

Uses Qwen3 tool calling to classify queries and expand search terms
in a single LLM call. Supports conversation history for context.
"""

import json
import os
from enum import Enum
from typing import Optional

import httpx
from loguru import logger
from pydantic import BaseModel, Field


class QueryIntent(str, Enum):
    """Query intent types for routing to appropriate handlers."""

    # Developer persona intents
    CODE_EXPLANATION = "code_explanation"      # "How does X work?"
    ARCHITECTURE = "architecture"              # "How is X organized?"
    IMPACT_ANALYSIS = "impact_analysis"        # "What depends on X?"
    SPECIFIC_LOOKUP = "specific_lookup"        # "Find function X"
    DOCUMENTATION = "documentation"            # "What are the guidelines for X?"

    # Sales persona intents
    CAPABILITY_CHECK = "capability_check"      # "Can we do X?"
    PROPOSAL_DRAFT = "proposal_draft"          # "Draft approach for X"
    EXPERIENCE_SUMMARY = "experience_summary"  # "What's our experience with X?"


class SearchDirection(str, Enum):
    """Controls progressive drilldown strategy."""

    BROAD = "broad"       # Start wide (repo/module), narrow if needed
    NARROW = "narrow"     # Start specific (file/symbol), widen if needed
    SPECIFIC = "specific" # Looking for exact named thing


class Persona(str, Enum):
    """Persona controls which intents and doc types are valid."""

    DEVELOPER = "developer"
    SALES = "sales"


# Intent to persona mapping
PERSONA_INTENTS = {
    Persona.DEVELOPER: {
        QueryIntent.CODE_EXPLANATION,
        QueryIntent.ARCHITECTURE,
        QueryIntent.IMPACT_ANALYSIS,
        QueryIntent.SPECIFIC_LOOKUP,
        QueryIntent.DOCUMENTATION,
    },
    Persona.SALES: {
        QueryIntent.CAPABILITY_CHECK,
        QueryIntent.PROPOSAL_DRAFT,
        QueryIntent.EXPERIENCE_SUMMARY,
    },
}


class ClassifiedIntent(BaseModel):
    """Result of intent classification."""

    intent: QueryIntent
    direction: SearchDirection
    entities: list[str] = Field(default_factory=list)
    search_keywords: list[str] = Field(default_factory=list)
    repo_scope: Optional[str] = None

    def expanded_query(self, original: str) -> str:
        """Combine original query with expanded keywords for embedding."""
        if not self.search_keywords:
            return original
        keywords = " ".join(self.search_keywords)
        return f"{original} {keywords}"


# Tool definition for Qwen3 function calling
def get_classify_tool(persona: Persona) -> dict:
    """Get tool definition filtered by persona's valid intents."""

    valid_intents = PERSONA_INTENTS[persona]
    intent_descriptions = {
        QueryIntent.CODE_EXPLANATION: "how does X work, explain implementation",
        QueryIntent.ARCHITECTURE: "how is X organized, structure overview",
        QueryIntent.IMPACT_ANALYSIS: "what depends on X, what breaks if changed",
        QueryIntent.SPECIFIC_LOOKUP: "find specific function/class by name",
        QueryIntent.DOCUMENTATION: "guidelines, principles, design docs",
        QueryIntent.CAPABILITY_CHECK: "can we do X, do we have capability",
        QueryIntent.PROPOSAL_DRAFT: "draft approach, write technical section",
        QueryIntent.EXPERIENCE_SUMMARY: "relevant experience, similar projects",
    }

    intent_enum = [i.value for i in valid_intents]
    intent_desc = " | ".join(
        f"{i.value} ({intent_descriptions[i]})"
        for i in valid_intents
    )

    return {
        "type": "function",
        "function": {
            "name": "classify_query",
            "description": "Classify the user's query intent and expand search terms",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": intent_enum,
                        "description": f"Query type: {intent_desc}"
                    },
                    "direction": {
                        "type": "string",
                        "enum": [d.value for d in SearchDirection],
                        "description": (
                            "Search strategy: "
                            "broad (overview questions, start at repo/module level) | "
                            "narrow (implementation details, start at file/symbol) | "
                            "specific (looking for exact named entity)"
                        )
                    },
                    "entities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Key entities mentioned: module names, features, concepts "
                            "(e.g., ['authentication', 'OAuth', 'UserModel'])"
                        )
                    },
                    "search_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "5-10 search keywords/phrases to find relevant content. "
                            "Include synonyms, related technical terms, different phrasings, "
                            "and both business and technical vocabulary."
                        )
                    },
                    "repo_scope": {
                        "type": "string",
                        "description": "Repository name if user specified one (e.g., 'kbhalerao/labcore'), null otherwise"
                    }
                },
                "required": ["intent", "direction", "entities", "search_keywords"]
            }
        }
    }


SYSTEM_PROMPTS = {
    Persona.DEVELOPER: """You are classifying developer queries about a codebase.

Given the conversation history and current query, determine:
1. What type of question is this? (code explanation, architecture, impact analysis, specific lookup, documentation)
2. Should we search broadly (overview) or narrowly (specific implementation)?
3. What are the key entities/concepts mentioned?
4. What search terms would help find relevant code?

For search_keywords, generate 5-10 terms including:
- The literal terms used in the query
- Technical synonyms (e.g., "auth" -> "authentication", "login", "session")
- Related concepts (e.g., "OAuth" -> "token", "JWT", "bearer")
- Code patterns (e.g., "decorator", "middleware", "handler")

Always call the classify_query function.""",

    Persona.SALES: """You are classifying business/sales queries about capabilities.

Given the conversation history and current query, determine:
1. What type of question is this? (capability check, proposal draft, experience summary)
2. Should we search broadly (capabilities overview) or narrowly (specific feature)?
3. What are the key business/technical concepts?
4. What search terms would help find relevant capabilities and past work?

For search_keywords, generate 5-10 terms including:
- Business terms the customer might use
- Technical terms that match capabilities
- Industry/domain vocabulary (e.g., "agriculture", "GIS", "soil sampling")
- Related features and integrations

Always call the classify_query function."""
}


class IntentClassifier:
    """Classifies queries using Qwen3 tool calling."""

    def __init__(
        self,
        lm_studio_url: str = None,
        model: str = "qwen/qwen3-30b-a3b-2507"
    ):
        self.lm_studio_url = lm_studio_url or os.getenv(
            "LMSTUDIO_URL", "http://localhost:1234"
        )
        self.model = model

    async def classify(
        self,
        query: str,
        persona: Persona = Persona.DEVELOPER,
        history: list[dict] = None
    ) -> ClassifiedIntent:
        """
        Classify query intent using Qwen3 tool calling.

        Args:
            query: The user's question
            persona: Developer or sales persona (controls valid intents)
            history: Last N conversation turns [{"role": "user"|"assistant", "content": "..."}]

        Returns:
            ClassifiedIntent with intent, direction, entities, and search keywords
        """
        # Build messages with conversation history
        messages = [{"role": "system", "content": SYSTEM_PROMPTS[persona]}]

        if history:
            # Last 3 turns = up to 6 messages
            recent = history[-6:]
            messages.extend(recent)

        messages.append({"role": "user", "content": query})

        tool = get_classify_tool(persona)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.lm_studio_url}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "tools": [tool],
                        "tool_choice": "required",  # LM Studio only supports string values
                        "temperature": 0.1,
                        "max_tokens": 300
                    }
                )
                response.raise_for_status()

                result = response.json()
                message = result["choices"][0]["message"]

                # Extract tool call arguments
                if "tool_calls" not in message or not message["tool_calls"]:
                    logger.warning("No tool call in response, using defaults")
                    return self._default_intent(query, persona)

                tool_call = message["tool_calls"][0]
                args = json.loads(tool_call["function"]["arguments"])

                # Validate intent is valid for persona
                intent = QueryIntent(args["intent"])
                if intent not in PERSONA_INTENTS[persona]:
                    logger.warning(f"Intent {intent} not valid for {persona}, using default")
                    intent = self._default_intent_for_persona(persona)

                classified = ClassifiedIntent(
                    intent=intent,
                    direction=SearchDirection(args["direction"]),
                    entities=args.get("entities", []),
                    search_keywords=args.get("search_keywords", []),
                    repo_scope=args.get("repo_scope")
                )

                logger.info(
                    f"Classified: intent={classified.intent.value} "
                    f"direction={classified.direction.value} "
                    f"keywords={len(classified.search_keywords)}"
                )

                return classified

        except httpx.HTTPError as e:
            logger.error(f"HTTP error during classification: {e}")
            return self._default_intent(query, persona)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Parse error during classification: {e}")
            return self._default_intent(query, persona)

    def _default_intent(self, query: str, persona: Persona) -> ClassifiedIntent:
        """Fallback when classification fails."""
        return ClassifiedIntent(
            intent=self._default_intent_for_persona(persona),
            direction=SearchDirection.NARROW,
            entities=[],
            search_keywords=query.split()[:10],  # Simple word split
            repo_scope=None
        )

    def _default_intent_for_persona(self, persona: Persona) -> QueryIntent:
        """Default intent per persona."""
        if persona == Persona.DEVELOPER:
            return QueryIntent.CODE_EXPLANATION
        else:
            return QueryIntent.CAPABILITY_CHECK
