"""
Synthesis Module for RAG Pipeline

Generates answers using Qwen3 with intent-specific prompts.
Loads templates from files for easy customization.
"""

import os
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger
from pydantic import BaseModel, Field

from app.rag.intent import ClassifiedIntent, QueryIntent, Persona
from app.rag.orchestrator import RetrievalResult
from app.rag.models import SearchResult


class SynthesisResult(BaseModel):
    """Result of synthesis step."""

    answer: str
    sources: list[str] = Field(default_factory=list)
    intent: QueryIntent
    levels_used: list[str] = Field(default_factory=list)
    adequate_context: bool = True
    gaps: list[str] = Field(default_factory=list)


# Default prompts embedded in code (can be overridden by files)
DEFAULT_PROMPTS = {
    QueryIntent.CODE_EXPLANATION: """You are explaining code from an indexed codebase.

## Retrieved Context
{context}

{parent_context}

## Question
{query}

## Instructions
- Explain how this code works based on the retrieved context
- Reference specific files and line numbers when available
- If the context is insufficient, say what's missing
- Use code blocks with syntax highlighting
- Be concise but thorough

## Answer""",

    QueryIntent.ARCHITECTURE: """You are explaining the architecture of a codebase.

## Retrieved Context
{context}

{parent_context}

## Question
{query}

## Instructions
- Describe the overall structure and organization
- Explain how components relate to each other
- Reference specific modules and their responsibilities
- If the context is insufficient, say what's missing

## Answer""",

    QueryIntent.IMPACT_ANALYSIS: """You are analyzing code dependencies and impact.

## Retrieved Context
{context}

{parent_context}

## Question
{query}

## Instructions
- Identify what depends on the code in question
- Explain potential impact of changes
- Reference specific files and modules affected
- If the context is insufficient, say what's missing

## Answer""",

    QueryIntent.SPECIFIC_LOOKUP: """You are finding specific code entities.

## Retrieved Context
{context}

{parent_context}

## Question
{query}

## Instructions
- Show the exact code that was requested
- Include file path and line numbers
- Provide brief explanation of what it does
- If not found, say so clearly

## Answer""",

    QueryIntent.DOCUMENTATION: """You are answering questions about documentation and guidelines.

## Retrieved Context
{context}

{parent_context}

## Question
{query}

## Instructions
- Answer based on the documentation provided
- Quote relevant sections when appropriate
- Reference document sources
- If the context is insufficient, say what's missing

## Answer""",

    QueryIntent.CAPABILITY_CHECK: """You are a technical advisor checking capabilities.

## Retrieved Context (from our codebase and capability briefs)
{context}

{parent_context}

## Customer Question
{query}

## Instructions
- Answer whether we can do what the customer is asking
- Cite specific projects or code that demonstrate the capability
- Be honest about gaps - mark as [GAP: description] if we don't have evidence
- Focus on business value, not just technical details
- If partially capable, explain what we have vs. what would need building

## Answer""",

    QueryIntent.PROPOSAL_DRAFT: """You are drafting a technical proposal section.

## Retrieved Context (from our codebase and capability briefs)
{context}

{parent_context}

## Request
{query}

## Instructions
- Write proposal-ready prose (can go directly into a document)
- Reference our proven capabilities and past work
- Be specific about approach based on our actual code patterns
- Mark gaps as [GAP: description] where we need more input
- Professional tone, no marketing fluff
- Structure with clear subsections if appropriate

## Draft""",

    QueryIntent.EXPERIENCE_SUMMARY: """You are summarizing relevant experience.

## Retrieved Context (from our codebase and capability briefs)
{context}

{parent_context}

## Request
{query}

## Instructions
- Summarize our relevant experience for the domain/technology
- Reference specific projects and capabilities
- Focus on outcomes and demonstrated expertise
- Mark gaps as [GAP: description] if experience is limited
- Suitable for including in proposals or presentations

## Summary""",
}


class Synthesizer:
    """
    Generates answers using Qwen3 with intent-specific prompts.
    """

    def __init__(
        self,
        lm_studio_url: str = None,
        model: str = "qwen/qwen3-30b-a3b-2507",
        prompts_dir: str = None,
    ):
        self.lm_studio_url = lm_studio_url or os.getenv(
            "LMSTUDIO_URL", "http://localhost:1234"
        )
        self.model = model

        # Prompts directory (optional, for file-based templates)
        if prompts_dir:
            self.prompts_dir = Path(prompts_dir)
        else:
            # Default to app/rag/prompts/
            self.prompts_dir = Path(__file__).parent / "prompts"

        self._prompt_cache: dict[QueryIntent, str] = {}

    def get_prompt_template(self, intent: QueryIntent) -> str:
        """Get prompt template for intent, preferring file over default."""
        if intent in self._prompt_cache:
            return self._prompt_cache[intent]

        # Try loading from file
        prompt_file = self.prompts_dir / f"{intent.value}.md"
        if prompt_file.exists():
            template = prompt_file.read_text()
            self._prompt_cache[intent] = template
            return template

        # Fall back to default
        template = DEFAULT_PROMPTS.get(intent, DEFAULT_PROMPTS[QueryIntent.CODE_EXPLANATION])
        self._prompt_cache[intent] = template
        return template

    async def synthesize(
        self,
        query: str,
        intent: ClassifiedIntent,
        retrieval: RetrievalResult,
        conversation_history: list[dict] = None,
    ) -> SynthesisResult:
        """
        Generate answer using Qwen3 with intent-specific prompt.

        Args:
            query: Original user query
            intent: Classified intent
            retrieval: Retrieved documents and metadata
            conversation_history: Optional conversation context

        Returns:
            SynthesisResult with answer and metadata
        """
        # Format retrieved context
        context = self._format_context(retrieval.results)

        # Format parent context if available
        parent_context = ""
        if retrieval.parent_context:
            parent_context = f"## Broader Context\n{retrieval.parent_context}"

        # Get and fill prompt template
        template = self.get_prompt_template(intent.intent)
        prompt = template.format(
            context=context,
            parent_context=parent_context,
            query=query,
        )

        # Build messages
        messages = []

        # Add conversation history for context
        if conversation_history:
            recent = conversation_history[-4:]  # Last 2 turns
            messages.extend(recent)

        messages.append({"role": "user", "content": prompt})

        # Call Qwen3
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.lm_studio_url}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": 0.3,
                        "max_tokens": 2000,
                    }
                )
                response.raise_for_status()

                result = response.json()
                answer = result["choices"][0]["message"]["content"]

                # Extract any [GAP: ...] markers
                gaps = self._extract_gaps(answer)

                # Build source list
                sources = [
                    f"{r.repo_id}/{r.file_path}" if r.file_path else r.repo_id
                    for r in retrieval.results
                    if r.repo_id
                ]

                logger.info(
                    f"Synthesis complete: {len(answer)} chars, {len(gaps)} gaps"
                )

                return SynthesisResult(
                    answer=answer,
                    sources=list(set(sources)),
                    intent=intent.intent,
                    levels_used=[l.value for l in retrieval.levels_searched],
                    adequate_context=retrieval.adequate,
                    gaps=gaps,
                )

        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            return SynthesisResult(
                answer=f"I encountered an error generating the answer: {str(e)}",
                sources=[],
                intent=intent.intent,
                levels_used=[l.value for l in retrieval.levels_searched],
                adequate_context=False,
                gaps=["Synthesis failed"],
            )

    def _format_context(self, results: list[SearchResult]) -> str:
        """Format search results as context for the prompt."""
        if not results:
            return "(No relevant documents found)"

        sections = []
        for i, r in enumerate(results, 1):
            # Build header
            if r.symbol_name:
                header = f"### {i}. {r.symbol_name} ({r.symbol_type or 'symbol'})"
                if r.file_path:
                    header += f" in {r.file_path}"
                if r.start_line and r.end_line:
                    header += f" [lines {r.start_line}-{r.end_line}]"
            elif r.file_path:
                header = f"### {i}. {r.file_path}"
            elif r.doc_type == "module_summary":
                header = f"### {i}. Module: {r.file_path or 'unknown'}/"
            elif r.doc_type == "repo_summary":
                header = f"### {i}. Repository: {r.repo_id}"
            elif r.doc_type == "repo_bdr":
                header = f"### {i}. Capability Brief: {r.repo_id}"
            else:
                header = f"### {i}. {r.repo_id}"

            # Add metadata line
            meta = f"_Repo: {r.repo_id} | Type: {r.doc_type} | Score: {r.score:.2f}_"

            # Truncate very long content
            content = r.content
            if len(content) > 2000:
                content = content[:2000] + "\n...[truncated]"

            sections.append(f"{header}\n{meta}\n\n{content}")

        return "\n\n---\n\n".join(sections)

    def _extract_gaps(self, answer: str) -> list[str]:
        """Extract [GAP: ...] markers from the answer."""
        import re
        gaps = re.findall(r'\[GAP:\s*([^\]]+)\]', answer)
        return gaps
