"""
Retrieval Orchestrator for RAG Pipeline

Handles progressive drilldown: if initial search results are inadequate,
automatically tries adjacent levels until good results are found.
"""

import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx
from loguru import logger
from sentence_transformers import SentenceTransformer

from app.database.couchbase_client import CouchbaseClient
from app.rag.intent import ClassifiedIntent, QueryIntent, SearchDirection, Persona
from app.rag.models import SearchLevel, SearchResult
from app.rag.reranker import CrossEncoderReranker
from app.rag.rewriter import QueryRewriter


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
    # Cross-persona intents
    QueryIntent.CHANGE_HISTORY: SearchLevel.COMMIT,
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


# OOD: if the top repo-level similarity is below this, the query is out of scope.
# Tune empirically against known in-scope vs OOD queries.
OOD_SCORE_THRESHOLD = float(os.getenv("RAG_OOD_THRESHOLD", "0.45"))

# Top-K repos from the REPO pass to use as the filter for subsequent levels.
ROUTE_TOP_K_REPOS = 5

# Per-analyzer cap for the lexical fallback (BM25 over repo_summary content).
# Two analyzers run (standard + en) and results are round-robin merged with
# vector hits, so total routed_repos is bounded by ROUTE_TOP_K_REPOS + 2 * cap.
LEXICAL_ROUTING_PER_ANALYZER = 3


@dataclass
class RetrievalResult:
    """Result of retrieval with drilldown metadata."""

    results: list[SearchResult] = field(default_factory=list)
    levels_searched: list[SearchLevel] = field(default_factory=list)
    adequate: bool = False
    parent_context: Optional[str] = None
    out_of_scope: bool = False
    top_repos: list[str] = field(default_factory=list)
    top_repo_score: float = 0.0


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
        rewriter: Optional[QueryRewriter] = None,
        reranker: Optional[CrossEncoderReranker] = None,
        bridge_enabled: bool = True,
    ):
        self.db = db
        self.embedding_model = embedding_model
        self.tenant_id = tenant_id
        self.min_good_results = min_good_results
        self.score_threshold = score_threshold
        self.rewriter = rewriter
        self.reranker = reranker
        self.bridge_enabled = bridge_enabled and os.getenv(
            "RAG_BRIDGE_ENABLED", "1"
        ) != "0"
        self.lexical_routing_enabled = os.getenv(
            "RAG_LEXICAL_ROUTING_ENABLED", "1"
        ) != "0"
        self.rerank_enabled = os.getenv("RAG_RERANK_ENABLED", "1") != "0"

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
        # Sales persona gets broader retrieval - more results, especially at REPO level
        if persona == Persona.SALES:
            limit = max(limit, 10)  # At least 10 results for sales

        # Use the raw user query for the routing embedding. Blind keyword
        # expansion via intent.search_keywords was diluting specific entity
        # queries with generic topic synonyms (e.g. "RBAC", "auth") and
        # pushing the actually-relevant repos out of the top-K. The bridge
        # step does grounded, data-driven rewriting later for code passes.
        query_embedding = self.embedding_model.encode(
            f"search_query: {query}",
            normalize_embeddings=True
        ).tolist()

        # ── Step 1: REPO pass — drives both OOD gate and routing ────────────
        # If the user named a specific repo, trust them and skip routing.
        if intent.repo_scope:
            routed_repos = [intent.repo_scope]
            repo_hits: list[SearchResult] = []
            top_repo_score = 1.0  # treat user-named scope as in-scope
            levels_searched: list[SearchLevel] = []
        else:
            repo_hits = await self._search_level(
                query_embedding=query_embedding,
                level=SearchLevel.REPO,
                repo_filter=None,
                limit=ROUTE_TOP_K_REPOS,
                persona=persona,
            )
            top_repo_score = max((r.score for r in repo_hits), default=0.0)
            levels_searched = [SearchLevel.REPO]

            # OOD gate
            if top_repo_score < OOD_SCORE_THRESHOLD:
                logger.info(
                    f"OOD: top repo score {top_repo_score:.3f} "
                    f"< {OOD_SCORE_THRESHOLD}"
                )
                return RetrievalResult(
                    levels_searched=levels_searched,
                    out_of_scope=True,
                    top_repos=[r.repo_id for r in repo_hits if r.repo_id],
                    top_repo_score=top_repo_score,
                )

            # Dedupe while preserving order — multiple repo_summary docs can
            # exist per repo (incremental ingestion artifact); the disjunction
            # would otherwise contain redundant terms.
            seen_repos: set[str] = set()
            routed_repos = [
                r.repo_id
                for r in repo_hits
                if r.repo_id and not (r.repo_id in seen_repos or seen_repos.add(r.repo_id))
            ]

            # Lexical complement to vector routing. Bi-encoder similarity
            # underweights rare/compound terms (e.g. "zoning" → "zonemaker")
            # and inconsistently surfaces exact-name matches against frequent
            # tokens in this corpus (e.g. "field"). Two BM25 passes —
            # standard analyzer for exact-name matches, English-stemmed for
            # morphological variants — fill those blind spots.
            if self.lexical_routing_enabled:
                lex_added = await self._lexical_repo_routing(
                    query=query,
                    exclude=set(routed_repos),
                    max_per_analyzer=LEXICAL_ROUTING_PER_ANALYZER,
                )
                if lex_added:
                    logger.info(f"Lexical routing added: {lex_added}")
                    routed_repos.extend(lex_added)

        # ── Step 2: build drilldown path (REPO already handled above) ───────
        start_level = INTENT_START_LEVELS.get(intent.intent, SearchLevel.FILE)

        if persona == Persona.SALES:
            base_path = [SearchLevel.MODULE, SearchLevel.DOC]
        else:
            base_path = list(DRILLDOWN_PATHS.get(intent.direction, [SearchLevel.FILE]))

        # Strip REPO from drilldown — we just did it for routing/OOD
        drilldown_path = [l for l in base_path if l != SearchLevel.REPO]

        # Honor the intent's preferred start level (unless it's REPO)
        if start_level != SearchLevel.REPO:
            if start_level in drilldown_path:
                drilldown_path.remove(start_level)
            drilldown_path.insert(0, start_level)

        logger.info(
            f"Retrieval: query='{query[:80]}...' "
            f"top_repo_score={top_repo_score:.3f} "
            f"routed_repos={routed_repos} "
            f"path={[l.value for l in drilldown_path]}"
        )

        # ── Step 3: progressive retrieval restricted to routed repos ────────
        # Sales surfaces the REPO hits directly; developer treats them as
        # routing signal only.
        all_results: list[SearchResult] = list(repo_hits) if persona == Persona.SALES else []
        matched_module_ids: list[str] = []
        current_embedding = query_embedding
        rewritten_query: Optional[str] = None

        for level in drilldown_path:
            # File-level passes after a successful bridge get narrowed to the
            # matched modules via parent_id. Symbol's parent is a file (not a
            # module), so we don't apply module ids there.
            pid_filter = matched_module_ids if level == SearchLevel.FILE else None

            results = await self._search_level(
                query_embedding=current_embedding,
                level=level,
                repo_filter=routed_repos or None,
                parent_id_filter=pid_filter or None,
                limit=limit,
                persona=persona,
            )

            levels_searched.append(level)
            all_results.extend(results)

            # Bridge: after a MODULE pass that returned content, translate the
            # query into the technical vocabulary of the matched modules so
            # downstream code-level passes hit better.
            if (
                level == SearchLevel.MODULE
                and results
                and self.bridge_enabled
                and self.rewriter is not None
            ):
                idx_in_path = drilldown_path.index(level)
                upcoming = drilldown_path[idx_in_path + 1:]
                if SearchLevel.FILE in upcoming or SearchLevel.SYMBOL in upcoming:
                    tech_context = [r.content for r in results[:3] if r.content]
                    rewritten_query = await self.rewriter.rewrite(query, tech_context)
                    if rewritten_query and rewritten_query != query:
                        current_embedding = self.embedding_model.encode(
                            f"search_query: {rewritten_query}",
                            normalize_embeddings=True,
                        ).tolist()
                    matched_module_ids = [r.document_id for r in results[:5]]
                    logger.info(
                        f"Bridge: rewrote={rewritten_query != query}, "
                        f"narrowing FILE pass to {len(matched_module_ids)} modules"
                    )

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

        # Sort by bi-encoder score descending (used for adequacy / fallback ordering)
        unique_results.sort(key=lambda r: r.score, reverse=True)

        # Cross-encoder rerank — joint query-passage scoring on the ORIGINAL
        # user query (not the bridge-rewritten one). Bridge handles recall;
        # original query determines true relevance.
        if self.reranker is not None and self.rerank_enabled and unique_results:
            unique_results = await self.reranker.rerank(query, unique_results)

        # Fetch parent context for grounding
        parent_context = await self._fetch_parent_context(unique_results[:limit])

        return RetrievalResult(
            results=unique_results[:limit],
            levels_searched=levels_searched,
            adequate=self._is_adequate(unique_results),
            parent_context=parent_context,
            out_of_scope=False,
            top_repos=routed_repos,
            top_repo_score=top_repo_score,
        )

    async def _search_level(
        self,
        query_embedding: list[float],
        level: SearchLevel,
        repo_filter: Optional[list[str]] = None,
        parent_id_filter: Optional[list[str]] = None,
        limit: int = 5,
        persona: Persona = Persona.DEVELOPER,
    ) -> list[SearchResult]:
        """Execute FTS search at a specific level.

        Note: With Couchbase 7.6.2, we use query+knn_operator which gives
        flat BM25 scores. We re-rank results using actual embedding similarity.
        """
        import numpy as np
        from app.rag.models import doc_types_for_level

        # For sales persona at REPO level, search both BDR and summary
        if persona == Persona.SALES and level == SearchLevel.REPO:
            doc_types = ["repo_bdr", "repo_summary"]
        else:
            doc_types = doc_types_for_level(level)

        # One query per document type — never a disjunct over `type`.
        #
        # A `disjuncts` type filter combined with knn_operator "and" silently
        # returns hits from only ONE clause. Measured: 'document' alone returns
        # 30/30 documents and 'spec' alone 30/30 specs, but the two as disjuncts
        # return 30/30 specs and zero documents at any k. The multi-type levels
        # here (SALES/REPO over repo_bdr + repo_summary, DOC over document +
        # spec) were therefore only ever searching one of their types.
        #
        # Note this applies to `type` specifically; the repo_id and parent_id
        # disjuncts below are unaffected, as they sit alongside a single-term
        # type clause rather than replacing it.
        def build_filter(doc_type: str) -> dict:
            """Filter query scoped to exactly one document type."""
            conjuncts: list[dict] = [{"term": doc_type, "field": "type"}]

            if repo_filter:
                conjuncts.append(
                    {"term": repo_filter[0], "field": "repo_id"}
                    if len(repo_filter) == 1
                    else {"disjuncts": [
                        {"term": rid, "field": "repo_id"} for rid in repo_filter
                    ]}
                )

            if parent_id_filter:
                conjuncts.append(
                    {"term": parent_id_filter[0], "field": "parent_id"}
                    if len(parent_id_filter) == 1
                    else {"disjuncts": [
                        {"term": pid, "field": "parent_id"} for pid in parent_id_filter
                    ]}
                )

            return conjuncts[0] if len(conjuncts) == 1 else {"conjuncts": conjuncts}

        # NOTE: Pre-filter inside knn requires Couchbase 7.6.4+
        # We have 7.6.2, so use query + knn_operator: "and" approach instead
        # KNOWN BUG: On 7.6.2, large k values (>~100) break the filter
        # Workaround: use smaller k and post-filter results in application code
        oversample = min(limit * 5, 100)  # Keep k <= 100 to avoid 7.6.2 bug

        def build_request(doc_type: str) -> dict:
            return {
                "query": build_filter(doc_type),
                "knn": [{
                    "field": "embedding",
                    "vector": query_embedding,
                    "k": oversample,
                }],
                "knn_operator": "and",
                "size": oversample,
                "fields": ["*"],
            }

        fts_url = f"http://{self.couchbase_host}:8094/api/index/code_vector_index/query"

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:

                async def knn_hits(doc_type: str) -> list:
                    response = await client.post(
                        fts_url,
                        auth=(self.couchbase_user, self.couchbase_pass),
                        json=build_request(doc_type),
                    )
                    response.raise_for_status()
                    return response.json().get("hits", [])

                hit_lists = await asyncio.gather(*(knn_hits(dt) for dt in doc_types))
                hits = [h for sub in hit_lists for h in sub]
                results = []

                # Convert query embedding to numpy for similarity computation
                query_vec = np.array(query_embedding)

                # Process returned hits for re-ranking (BM25 scores are flat)
                # Post-filter to ensure only valid doc types (workaround for 7.6.2 bug)
                doc_types_set = set(doc_types)
                repo_filter_set = set(repo_filter) if repo_filter else None
                for hit in hits:
                    doc_id = hit.get("id")
                    if not doc_id:
                        continue

                    try:
                        doc = await self.db.get_doc(self.tenant_id, doc_id)
                        if doc is None:
                            continue

                        # Post-filter: skip documents that don't match expected types
                        # or that leaked through the repo_id filter
                        # (workaround for Couchbase 7.6.2 bug with large k values
                        # and many-disjunct filters).
                        actual_type = doc.get("type")
                        if actual_type not in doc_types_set:
                            continue
                        if repo_filter_set is not None and doc.get("repo_id") not in repo_filter_set:
                            continue

                        metadata = doc.get("metadata", {})

                        # Compute true cosine similarity instead of using FTS score
                        # (BM25 dominates in query+knn mode, giving flat scores)
                        doc_embedding = doc.get("embedding")
                        if doc_embedding:
                            doc_vec = np.array(doc_embedding)
                            # Embeddings are normalized, so dot product = cosine similarity
                            similarity = float(np.dot(query_vec, doc_vec))
                        else:
                            similarity = hit.get("score", 0.0)

                        actual_type = doc.get("type", doc_types[0])
                        if actual_type == "commit_index":
                            results.append(SearchResult(
                                document_id=doc_id,
                                doc_type=actual_type,
                                repo_id=doc.get("repo_id", ""),
                                content=doc.get("content", ""),
                                score=similarity,
                                commit_hash=doc.get("commit_hash"),
                                author=doc.get("author"),
                                commit_date=doc.get("commit_date"),
                            ))
                        else:
                            results.append(SearchResult(
                                document_id=doc_id,
                                doc_type=actual_type,
                                repo_id=doc.get("repo_id", ""),
                                file_path=doc.get("file_path") or doc.get("module_path"),
                                symbol_name=doc.get("symbol_name"),
                                symbol_type=doc.get("symbol_type") or doc.get("doc_type"),
                                content=doc.get("content", ""),
                                score=similarity,
                                parent_id=doc.get("parent_id"),
                                children_ids=doc.get("children_ids", []),
                                start_line=metadata.get("start_line"),
                                end_line=metadata.get("end_line"),
                            ))
                    except Exception as e:
                        logger.warning(f"Failed to fetch document {doc_id}: {e}")
                        continue

                # Re-sort by true embedding similarity (descending) and take top `limit`
                results.sort(key=lambda r: r.score, reverse=True)
                results = results[:limit]

                logger.info(f"Search {level.value}: {len(results)} results (from {len(hits)} candidates)")
                return results

        except Exception as e:
            logger.error(f"FTS search failed at {level.value}: {e}")
            return []

    async def _lex_query(
        self,
        query: str,
        analyzer: Optional[str],
        n: int,
    ) -> list[str]:
        """Single BM25 pass over repo_summary content. Returns deduped
        repo_ids in BM25-rank order."""
        match_clause: dict = {"match": query, "field": "content"}
        if analyzer:
            match_clause["analyzer"] = analyzer

        fts_request = {
            "query": {
                "conjuncts": [
                    {"term": "repo_summary", "field": "type"},
                    match_clause,
                ]
            },
            "size": n * 4,  # oversample for repo-level dedup
            "fields": ["repo_id"],
        }
        fts_url = f"http://{self.couchbase_host}:8094/api/index/code_vector_index/query"

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    fts_url,
                    auth=(self.couchbase_user, self.couchbase_pass),
                    json=fts_request,
                )
                response.raise_for_status()
                hits = response.json().get("hits", [])
        except Exception as e:
            logger.warning(f"Lexical match (analyzer={analyzer}) failed: {e}")
            return []

        seen: set[str] = set()
        out: list[str] = []
        for h in hits:
            rid = (h.get("fields") or {}).get("repo_id")
            if rid and rid not in seen:
                seen.add(rid)
                out.append(rid)
                if len(out) >= n:
                    break
        return out

    async def _lexical_repo_routing(
        self,
        query: str,
        exclude: set[str],
        max_per_analyzer: int = 3,
    ) -> list[str]:
        """Hybrid lexical routing — runs two parallel BM25 passes over
        repo_summary content (standard + English-stemmed) and round-robin
        merges the results, excluding repos already routed by the vector
        pass. Compensates for bi-encoder blind spots on rare/compound terms
        and exact-name matches in a noisy frequent-token corpus."""
        std_hits, en_hits = await asyncio.gather(
            self._lex_query(query, None, max_per_analyzer),
            self._lex_query(query, "en", max_per_analyzer),
        )

        merged: list[str] = []
        seen: set[str] = set(exclude)
        for i in range(max_per_analyzer):
            for source in (std_hits, en_hits):
                if i < len(source) and source[i] not in seen:
                    merged.append(source[i])
                    seen.add(source[i])
        return merged

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
        parent_summaries = []
        for parent_id in list(parent_ids)[:3]:  # Limit to 3 parents
            try:
                doc = await self.db.get_doc(self.tenant_id, parent_id)
                if doc is None:
                    continue
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
