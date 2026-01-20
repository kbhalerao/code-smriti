"""
Unified RAG Pipeline

Ties together intent classification, retrieval orchestration, and synthesis
into a single coherent pipeline. Supports both developer and sales personas.
"""

import os
from typing import Optional

from loguru import logger
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

from app.database.couchbase_client import CouchbaseClient
from app.rag.intent import (
    IntentClassifier,
    ClassifiedIntent,
    QueryIntent,
    Persona,
)
from app.rag.orchestrator import RetrievalOrchestrator, RetrievalResult
from app.rag.synthesis import Synthesizer, SynthesisResult


class PipelineResult(BaseModel):
    """Complete result from RAG pipeline."""

    answer: str
    intent: QueryIntent
    direction: str
    entities: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    levels_searched: list[str] = Field(default_factory=list)
    adequate_context: bool = True
    gaps: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


# Singleton for embedding model
_embedding_model: Optional[SentenceTransformer] = None


def get_embedding_model(
    model_name: str = "nomic-ai/nomic-embed-text-v1.5"
) -> SentenceTransformer:
    """Get or create embedding model (singleton)."""
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Loading embedding model: {model_name}")
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        _embedding_model = SentenceTransformer(model_name, trust_remote_code=True)
        logger.info("Embedding model loaded")
    return _embedding_model


class RAGPipeline:
    """
    Unified RAG pipeline with intent classification, progressive retrieval,
    and intent-specific synthesis.

    Usage:
        pipeline = RAGPipeline(db, tenant_id="code_kosha")

        # Developer query
        result = await pipeline.run(
            "How does authentication work?",
            persona=Persona.DEVELOPER
        )

        # Sales query
        result = await pipeline.run(
            "Can we build a GIS platform?",
            persona=Persona.SALES
        )
    """

    def __init__(
        self,
        db: CouchbaseClient,
        tenant_id: str = "code_kosha",
        lm_studio_url: str = None,
        llm_model: str = "qwen/qwen3-30b-a3b-2507",
        embedding_model_name: str = "nomic-ai/nomic-embed-text-v1.5",
    ):
        self.db = db
        self.tenant_id = tenant_id

        lm_studio_url = lm_studio_url or os.getenv(
            "LMSTUDIO_URL", "http://localhost:1234"
        )

        # Initialize components
        self.classifier = IntentClassifier(
            lm_studio_url=lm_studio_url,
            model=llm_model,
        )

        self.embedding_model = get_embedding_model(embedding_model_name)

        self.orchestrator = RetrievalOrchestrator(
            db=db,
            embedding_model=self.embedding_model,
            tenant_id=tenant_id,
        )

        self.synthesizer = Synthesizer(
            lm_studio_url=lm_studio_url,
            model=llm_model,
        )

    async def run(
        self,
        query: str,
        persona: Persona = Persona.DEVELOPER,
        conversation_history: list[dict] = None,
        limit: int = 5,
    ) -> PipelineResult:
        """
        Run the full RAG pipeline.

        Args:
            query: User's question
            persona: Developer or sales persona
            conversation_history: Previous turns for context
            limit: Max documents to retrieve

        Returns:
            PipelineResult with answer and metadata
        """
        logger.info(f"Pipeline: persona={persona.value} query='{query[:80]}...'")

        # Step 1: Intent Classification (Qwen3 tool call, ~2-3s)
        intent = await self.classifier.classify(
            query=query,
            persona=persona,
            history=conversation_history,
        )

        logger.info(
            f"Classified: intent={intent.intent.value} "
            f"direction={intent.direction.value} "
            f"entities={intent.entities}"
        )

        # Step 2: Retrieval with Progressive Drilldown (~2-5s)
        retrieval = await self.orchestrator.retrieve(
            query=query,
            intent=intent,
            persona=persona,
            limit=limit,
        )

        logger.info(
            f"Retrieved: {len(retrieval.results)} docs "
            f"levels={[l.value for l in retrieval.levels_searched]} "
            f"adequate={retrieval.adequate}"
        )

        # Step 3: Synthesis (Qwen3, ~5-15s depending on response length)
        synthesis = await self.synthesizer.synthesize(
            query=query,
            intent=intent,
            retrieval=retrieval,
            conversation_history=conversation_history,
        )

        logger.info(
            f"Synthesized: {len(synthesis.answer)} chars "
            f"gaps={len(synthesis.gaps)}"
        )

        return PipelineResult(
            answer=synthesis.answer,
            intent=intent.intent,
            direction=intent.direction.value,
            entities=intent.entities,
            sources=synthesis.sources,
            levels_searched=synthesis.levels_used,
            adequate_context=synthesis.adequate_context,
            gaps=synthesis.gaps,
            metadata={
                "search_keywords": intent.search_keywords,
                "repo_scope": intent.repo_scope,
                "persona": persona.value,
            }
        )

    async def run_developer(
        self,
        query: str,
        conversation_history: list[dict] = None,
    ) -> PipelineResult:
        """Convenience method for developer persona."""
        return await self.run(
            query=query,
            persona=Persona.DEVELOPER,
            conversation_history=conversation_history,
        )

    async def run_sales(
        self,
        query: str,
        conversation_history: list[dict] = None,
    ) -> PipelineResult:
        """Convenience method for sales persona."""
        return await self.run(
            query=query,
            persona=Persona.SALES,
            conversation_history=conversation_history,
        )


# Factory function for easy instantiation
def create_pipeline(
    db: CouchbaseClient,
    tenant_id: str = "code_kosha",
) -> RAGPipeline:
    """Create a RAGPipeline with default configuration."""
    return RAGPipeline(
        db=db,
        tenant_id=tenant_id,
        lm_studio_url=os.getenv("LMSTUDIO_URL", "http://localhost:1234"),
        llm_model=os.getenv("LMSTUDIO_MODEL", "qwen/qwen3-30b-a3b-2507"),
        embedding_model_name=os.getenv(
            "EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5"
        ),
    )
