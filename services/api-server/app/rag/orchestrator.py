"""
Retrieval Orchestrator for RAG Pipeline

Handles progressive drilldown: if initial search results are inadequate,
automatically tries adjacent levels until good results are found.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

import httpx
from loguru import logger
from sentence_transformers import SentenceTransformer

from app.database.couchbase_client import CouchbaseClient
from app.rag.intent import ClassifiedIntent, QueryIntent, SearchDirection, Persona
from app.rag.models import SearchLevel, SearchResult


# Map intents to starting search levels
INTENT_START_LEVELS = {
    # Developer intents
    QueryIntent.CODE_EXPLANATION: SearchLevel.FILE,
    QueryIntent.ARCHITECTURE: SearchLevel.MODULE,
    QueryIntent.IMPACT_ANALYSIS: SearchLevel.FILE,
    QueryIntent.SPECIFIC_LOOKUP: SearchLevel.SYMBOL,
    QueryIntent.DOCUMENTATION: SearchLevel.DOC,
    # Sales intents
    QueryIntent.CAPABILITY_CHECK: SearchLevel.REPO,
    QueryIntent.PROPOSAL_DRAFT: SearchLevel.MODULE,
    QueryIntent.EXPERIENCE_SUMMARY: SearchLevel.REPO,
}


# Drilldown paths by search direction
DRILLDOWN_PATHS = {
    SearchDirection.BROAD: [
        SearchLevel.REPO,
        SearchLevel.MODULE,
        SearchLevel.FILE,
    ],
    SearchDirection.NARROW: [
        SearchLevel.FILE,
        SearchLevel.SYMBOL,
        SearchLevel.MODULE,
    ],
    SearchDirection.SPECIFIC: [
        SearchLevel.SYMBOL,
        SearchLevel.FILE,
    ],
}


# Doc types to search by persona (for multi-type searches)
PERSONA_DOC_TYPES = {
    Persona.DEVELOPER: ["file_index", "symbol_index", "module_summary", "document"],
    Persona.SALES: ["repo_bdr", "repo_summary", "document", "module_summary"],
}


@dataclass
class RetrievalResult:
    """Result of retrieval with drilldown metadata."""

    results: list[SearchResult] = field(default_factory=list)
    levels_searched: list[SearchLevel] = field(default_factory=list)
    adequate: bool = False
    parent_context: Optional[str] = None


class RetrievalOrchestrator:
    """
    Orchestrates retrieval with progressive drilldown.

    When initial results are inadequate, automatically tries adjacent
    search levels based on the query's direction (broad/narrow/specific).
    """

    def __init__(
        self,
        db: CouchbaseClient,
        embedding_model: SentenceTransformer,
        tenant_id: str = "code_kosha",
        min_good_results: int = 2,
        score_threshold: float = 0.65,
    ):
        self.db = db
        self.embedding_model = embedding_model
        self.tenant_id = tenant_id
        self.min_good_results = min_good_results
        self.score_threshold = score_threshold

        self.couchbase_host = os.getenv("COUCHBASE_HOST", "localhost")
        self.couchbase_user = os.getenv("COUCHBASE_USERNAME", "Administrator")
        self.couchbase_pass = os.environ.get("COUCHBASE_PASSWORD", "")

    async def retrieve(
        self,
        query: str,
        intent: ClassifiedIntent,
        persona: Persona = Persona.DEVELOPER,
        limit: int = 5,
    ) -> RetrievalResult:
        """
        Retrieve relevant documents with progressive drilldown.

        Args:
            query: Original user query
            intent: Classified intent with expanded keywords
            persona: Developer or sales (controls doc types)
            limit: Max results per level

        Returns:
            RetrievalResult with documents and metadata
        """
        # Expand query with keywords from classification
        expanded_query = intent.expanded_query(query)

        # Generate embedding for expanded query
        query_embedding = self.embedding_model.encode(
            f"search_query: {expanded_query}",
            normalize_embeddings=True
        ).tolist()

        # Get starting level and drilldown path
        start_level = INTENT_START_LEVELS.get(intent.intent, SearchLevel.FILE)
        drilldown_path = list(DRILLDOWN_PATHS.get(intent.direction, [SearchLevel.FILE]))

        # Ensure start level is first in path
        if start_level in drilldown_path:
            drilldown_path.remove(start_level)
        drilldown_path.insert(0, start_level)

        logger.info(
            f"Retrieval: expanded_query='{expanded_query[:80]}...' "
            f"start={start_level.value} path={[l.value for l in drilldown_path]}"
        )

        # Progressive retrieval
        all_results: list[SearchResult] = []
        levels_searched: list[SearchLevel] = []

        for level in drilldown_path:
            results = await self._search_level(
                query_embedding=query_embedding,
                level=level,
                repo_filter=intent.repo_scope,
                limit=limit,
            )

            levels_searched.append(level)
            all_results.extend(results)

            # Check if we have adequate results
            if self._is_adequate(all_results):
                logger.info(f"Adequate results after {level.value} level")
                break

            logger.info(f"Inadequate results at {level.value}, continuing drilldown")

        # Deduplicate by document ID
        seen_ids = set()
        unique_results = []
        for r in all_results:
            if r.document_id not in seen_ids:
                seen_ids.add(r.document_id)
                unique_results.append(r)

        # Sort by score descending
        unique_results.sort(key=lambda r: r.score, reverse=True)

        # Fetch parent context for grounding
        parent_context = await self._fetch_parent_context(unique_results[:limit])

        return RetrievalResult(
            results=unique_results[:limit],
            levels_searched=levels_searched,
            adequate=self._is_adequate(unique_results),
            parent_context=parent_context,
        )

    async def _search_level(
        self,
        query_embedding: list[float],
        level: SearchLevel,
        repo_filter: Optional[str],
        limit: int,
    ) -> list[SearchResult]:
        """Execute FTS search at a specific level."""
        from app.rag.models import LEVEL_TO_DOCTYPE

        doc_type = LEVEL_TO_DOCTYPE[level]

        # Build filter
        filter_query = {"term": doc_type, "field": "type"}
        if repo_filter:
            filter_query = {
                "conjuncts": [
                    {"term": doc_type, "field": "type"},
                    {"term": repo_filter, "field": "repo_id"},
                ]
            }

        fts_request = {
            "query": filter_query,
            "knn": [{
                "field": "embedding",
                "vector": query_embedding,
                "k": limit * 2,
            }],
            "knn_operator": "and",
            "size": limit * 2,
            "fields": ["*"],
        }

        fts_url = f"http://{self.couchbase_host}:8094/api/index/code_vector_index/query"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    fts_url,
                    auth=(self.couchbase_user, self.couchbase_pass),
                    json=fts_request,
                )
                response.raise_for_status()

                hits = response.json().get("hits", [])
                results = []

                # Fetch full documents
                bucket = self.db.cluster.bucket(self.tenant_id)
                collection = bucket.default_collection()

                for hit in hits[:limit]:
                    doc_id = hit.get("id")
                    if not doc_id:
                        continue

                    try:
                        doc_result = collection.get(doc_id)
                        doc = doc_result.content_as[dict]
                        metadata = doc.get("metadata", {})

                        results.append(SearchResult(
                            document_id=doc_id,
                            doc_type=doc.get("type", doc_type),
                            repo_id=doc.get("repo_id", ""),
                            file_path=doc.get("file_path") or doc.get("module_path"),
                            symbol_name=doc.get("symbol_name"),
                            symbol_type=doc.get("symbol_type") or doc.get("doc_type"),
                            content=doc.get("content", ""),
                            score=hit.get("score", 0.0),
                            parent_id=doc.get("parent_id"),
                            children_ids=doc.get("children_ids", []),
                            start_line=metadata.get("start_line"),
                            end_line=metadata.get("end_line"),
                        ))
                    except Exception as e:
                        logger.warning(f"Failed to fetch document {doc_id}: {e}")
                        continue

                logger.info(f"Search {level.value}: {len(results)} results")
                return results

        except Exception as e:
            logger.error(f"FTS search failed at {level.value}: {e}")
            return []

    def _is_adequate(self, results: list[SearchResult]) -> bool:
        """Check if results are adequate to answer the query."""
        if not results:
            return False

        high_score = [r for r in results if r.score >= self.score_threshold]
        return len(high_score) >= self.min_good_results

    async def _fetch_parent_context(
        self,
        results: list[SearchResult],
    ) -> Optional[str]:
        """
        Fetch parent summaries for grounding.

        When returning file/symbol results, include parent module summary
        so the LLM understands the broader context.
        """
        if not results:
            return None

        # Collect unique parent IDs
        parent_ids = set()
        for r in results:
            if r.parent_id:
                parent_ids.add(r.parent_id)

        if not parent_ids:
            return None

        # Fetch parent documents
        bucket = self.db.cluster.bucket(self.tenant_id)
        collection = bucket.default_collection()

        parent_summaries = []
        for parent_id in list(parent_ids)[:3]:  # Limit to 3 parents
            try:
                doc_result = collection.get(parent_id)
                doc = doc_result.content_as[dict]
                content = doc.get("content", "")
                doc_type = doc.get("type", "")
                path = doc.get("module_path") or doc.get("file_path") or doc.get("repo_id")

                if content:
                    parent_summaries.append(f"[{doc_type}] {path}:\n{content[:500]}")
            except Exception as e:
                logger.warning(f"Failed to fetch parent {parent_id}: {e}")
                continue

        if parent_summaries:
            return "\n\n".join(parent_summaries)

        return None
