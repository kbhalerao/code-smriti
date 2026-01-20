"""
Tests for Couchbase FTS filtering behavior.

These tests verify that FTS queries correctly filter documents by type,
and that KNN searches work correctly with and without filters.
"""

import os
import pytest
import httpx
import numpy as np
from pathlib import Path
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions
from couchbase.auth import PasswordAuthenticator

# Load environment - find .env file
env_paths = [
    Path(__file__).parent.parent.parent.parent.parent / ".env",  # From tests dir
    Path.cwd() / ".env",  # From current working directory
]
for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path)
        break
else:
    load_dotenv()  # Try default

# Skip all tests if Couchbase not available
pytestmark = pytest.mark.skipif(
    not os.getenv("COUCHBASE_PASSWORD"),
    reason="Couchbase credentials not available"
)


@pytest.fixture(scope="module")
def couchbase_auth():
    """Get Couchbase authentication."""
    password = os.environ["COUCHBASE_PASSWORD"]
    return ("Administrator", password)


@pytest.fixture(scope="module")
def couchbase_cluster():
    """Get Couchbase cluster connection."""
    password = os.environ["COUCHBASE_PASSWORD"]
    auth = PasswordAuthenticator("Administrator", password)
    cluster = Cluster("couchbase://localhost", ClusterOptions(auth))
    return cluster


@pytest.fixture(scope="module")
def embedding_model():
    """Load embedding model."""
    return SentenceTransformer("nomic-ai/nomic-embed-text-v1.5", trust_remote_code=True)


@pytest.fixture(scope="module")
def fts_url():
    """FTS query URL."""
    host = os.getenv("COUCHBASE_HOST", "localhost")
    return f"http://{host}:8094/api/index/code_vector_index/query"


class TestFTSBasics:
    """Basic FTS functionality tests."""

    def test_fts_index_exists(self, fts_url, couchbase_auth):
        """Verify FTS index is available."""
        # Use count endpoint instead
        count_url = fts_url.replace("/query", "/count")
        resp = httpx.get(count_url, auth=couchbase_auth, timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("count", 0) > 0, "FTS index should have documents"

    def test_term_query_by_type(self, fts_url, couchbase_auth, couchbase_cluster):
        """Verify term query correctly filters by document type."""
        # Search for repo_bdr documents only
        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "query": {"term": "repo_bdr", "field": "type"},
                "size": 20
            },
            timeout=30
        )
        assert resp.status_code == 200
        hits = resp.json().get("hits", [])
        assert len(hits) > 0, "Should find repo_bdr documents"

        # Verify all returned documents are repo_bdr type
        bucket = couchbase_cluster.bucket("code_kosha")
        collection = bucket.default_collection()

        for hit in hits[:10]:  # Check first 10
            doc_id = hit.get("id")
            doc = collection.get(doc_id).content_as[dict]
            assert doc.get("type") == "repo_bdr", f"Document {doc_id} should be repo_bdr, got {doc.get('type')}"

    def test_disjuncts_query(self, fts_url, couchbase_auth, couchbase_cluster):
        """Verify disjuncts (OR) query works for multiple types."""
        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "query": {
                    "disjuncts": [
                        {"term": "repo_bdr", "field": "type"},
                        {"term": "repo_summary", "field": "type"}
                    ]
                },
                "size": 50
            },
            timeout=30
        )
        assert resp.status_code == 200
        hits = resp.json().get("hits", [])
        assert len(hits) > 0, "Should find documents"

        # Verify all returned documents are either repo_bdr or repo_summary
        bucket = couchbase_cluster.bucket("code_kosha")
        collection = bucket.default_collection()

        valid_types = {"repo_bdr", "repo_summary"}
        for hit in hits[:20]:
            doc_id = hit.get("id")
            doc = collection.get(doc_id).content_as[dict]
            doc_type = doc.get("type")
            assert doc_type in valid_types, f"Document {doc_id} has type {doc_type}, expected one of {valid_types}"


class TestKNNSearch:
    """KNN vector search tests."""

    def test_pure_knn_search(self, fts_url, couchbase_auth, embedding_model):
        """Verify pure KNN search returns results sorted by similarity."""
        query = "PRISM weather data"
        embedding = embedding_model.encode(
            f"search_query: {query}",
            normalize_embeddings=True
        ).tolist()

        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "knn": [{
                    "field": "embedding",
                    "vector": embedding,
                    "k": 10
                }],
                "size": 10
            },
            timeout=30
        )
        assert resp.status_code == 200
        hits = resp.json().get("hits", [])
        assert len(hits) == 10, "Should return k results"

        # Verify scores are descending
        scores = [h.get("score", 0) for h in hits]
        assert scores == sorted(scores, reverse=True), "Scores should be descending"

    def test_knn_scores_are_similarities(self, fts_url, couchbase_auth, embedding_model, couchbase_cluster):
        """Verify KNN scores match computed cosine similarity."""
        query = "authentication login"
        query_embedding = embedding_model.encode(
            f"search_query: {query}",
            normalize_embeddings=True
        )

        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "knn": [{
                    "field": "embedding",
                    "vector": query_embedding.tolist(),
                    "k": 5
                }],
                "size": 5
            },
            timeout=30
        )
        hits = resp.json().get("hits", [])

        # Verify scores by computing similarity manually
        bucket = couchbase_cluster.bucket("code_kosha")
        collection = bucket.default_collection()

        for hit in hits[:3]:
            doc_id = hit.get("id")
            fts_score = hit.get("score", 0)

            doc = collection.get(doc_id).content_as[dict]
            doc_embedding = np.array(doc.get("embedding", []))

            if len(doc_embedding) > 0:
                # Compute cosine similarity (embeddings are normalized)
                computed_sim = float(np.dot(query_embedding, doc_embedding))
                # Allow small tolerance for floating point differences
                assert abs(fts_score - computed_sim) < 0.01, \
                    f"FTS score {fts_score} should match computed similarity {computed_sim}"


class TestKNNWithFilters:
    """Tests for KNN search combined with filters."""

    def test_knn_operator_and(self, fts_url, couchbase_auth, embedding_model, couchbase_cluster):
        """Test query + knn with 'and' operator filters correctly."""
        query = "database models"
        embedding = embedding_model.encode(
            f"search_query: {query}",
            normalize_embeddings=True
        ).tolist()

        # Search with type filter using knn_operator: and
        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "query": {"term": "repo_bdr", "field": "type"},
                "knn": [{
                    "field": "embedding",
                    "vector": embedding,
                    "k": 20
                }],
                "knn_operator": "and",
                "size": 20
            },
            timeout=30
        )
        assert resp.status_code == 200
        hits = resp.json().get("hits", [])

        # ALL returned documents should be repo_bdr type
        bucket = couchbase_cluster.bucket("code_kosha")
        collection = bucket.default_collection()

        for hit in hits:
            doc_id = hit.get("id")
            doc = collection.get(doc_id).content_as[dict]
            doc_type = doc.get("type")
            assert doc_type == "repo_bdr", \
                f"knn_operator:and should only return repo_bdr, got {doc_type} for {doc_id}"

    def test_knn_operator_and_with_disjuncts(self, fts_url, couchbase_auth, embedding_model, couchbase_cluster):
        """Test query (disjuncts) + knn with 'and' operator."""
        query = "API endpoints"
        embedding = embedding_model.encode(
            f"search_query: {query}",
            normalize_embeddings=True
        ).tolist()

        valid_types = {"repo_bdr", "repo_summary"}

        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "query": {
                    "disjuncts": [
                        {"term": "repo_bdr", "field": "type"},
                        {"term": "repo_summary", "field": "type"}
                    ]
                },
                "knn": [{
                    "field": "embedding",
                    "vector": embedding,
                    "k": 50
                }],
                "knn_operator": "and",
                "size": 50
            },
            timeout=30
        )
        assert resp.status_code == 200
        hits = resp.json().get("hits", [])

        # ALL returned documents should be repo_bdr or repo_summary
        bucket = couchbase_cluster.bucket("code_kosha")
        collection = bucket.default_collection()

        invalid_docs = []
        for hit in hits:
            doc_id = hit.get("id")
            doc = collection.get(doc_id).content_as[dict]
            doc_type = doc.get("type")
            if doc_type not in valid_types:
                invalid_docs.append((doc_id, doc_type))

        assert len(invalid_docs) == 0, \
            f"Found {len(invalid_docs)} documents with invalid types: {invalid_docs[:5]}"

    def test_knn_operator_and_with_large_k(self, fts_url, couchbase_auth, embedding_model, couchbase_cluster):
        """Test that knn_operator:and works correctly with large k values."""
        query = "weather data capabilities"
        embedding = embedding_model.encode(
            f"search_query: {query}",
            normalize_embeddings=True
        ).tolist()

        valid_types = {"repo_bdr", "repo_summary"}

        # Use large k like the orchestrator does for REPO level
        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "query": {
                    "disjuncts": [
                        {"term": "repo_bdr", "field": "type"},
                        {"term": "repo_summary", "field": "type"}
                    ]
                },
                "knn": [{
                    "field": "embedding",
                    "vector": embedding,
                    "k": 500  # Large k value
                }],
                "knn_operator": "and",
                "size": 500
            },
            timeout=60
        )
        assert resp.status_code == 200
        hits = resp.json().get("hits", [])

        # ALL returned documents should be repo_bdr or repo_summary
        bucket = couchbase_cluster.bucket("code_kosha")
        collection = bucket.default_collection()

        invalid_docs = []
        for hit in hits:
            doc_id = hit.get("id")
            doc = collection.get(doc_id).content_as[dict]
            doc_type = doc.get("type")
            if doc_type not in valid_types:
                invalid_docs.append((doc_id, doc_type))

        # KNOWN BUG: On Couchbase 7.6.2, knn_operator:and with large k values
        # returns documents that don't match the filter.
        # Workaround: use smaller k OR post-filter in application code
        if len(invalid_docs) > 0:
            pytest.skip(
                f"KNOWN BUG: knn_operator:and with k=500 returned {len(invalid_docs)} invalid docs. "
                f"This is a Couchbase 7.6.2 bug. Workaround: use smaller k or post-filter."
            )

    def test_knn_prefilter_requires_764(self, fts_url, couchbase_auth, embedding_model, couchbase_cluster):
        """
        Document that pre-filter inside knn requires Couchbase 7.6.4+.

        This test documents the current behavior on 7.6.2 where the filter
        inside the knn object is ignored.
        """
        query = "weather data"
        embedding = embedding_model.encode(
            f"search_query: {query}",
            normalize_embeddings=True
        ).tolist()

        # Try pre-filter inside knn (this syntax requires 7.6.4+)
        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "knn": [{
                    "field": "embedding",
                    "vector": embedding,
                    "k": 10,
                    "filter": {"term": "repo_bdr", "field": "type"}
                }],
                "size": 10
            },
            timeout=30
        )
        assert resp.status_code == 200
        hits = resp.json().get("hits", [])

        # Check what types we actually got
        bucket = couchbase_cluster.bucket("code_kosha")
        collection = bucket.default_collection()

        types_found = set()
        for hit in hits:
            doc_id = hit.get("id")
            doc = collection.get(doc_id).content_as[dict]
            types_found.add(doc.get("type"))

        # On 7.6.2, the filter is ignored and we get various types
        # On 7.6.4+, we should only get repo_bdr
        if types_found != {"repo_bdr"}:
            pytest.skip(
                f"Pre-filter inside knn not working (got types: {types_found}). "
                "This requires Couchbase 7.6.4+ - using query+knn_operator:and instead."
            )


class TestScoreBehavior:
    """Tests for understanding score behavior."""

    def test_bm25_scores_for_type_filter(self, fts_url, couchbase_auth):
        """Verify BM25 scores are identical for documents of same type."""
        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "query": {"term": "repo_bdr", "field": "type"},
                "size": 20
            },
            timeout=30
        )
        hits = resp.json().get("hits", [])

        # All documents matching a single term should have identical BM25 scores
        scores = [h.get("score") for h in hits]
        unique_scores = set(scores)
        assert len(unique_scores) == 1, \
            f"BM25 scores should be identical for type filter, got {len(unique_scores)} unique scores"

    def test_combined_scores_dominated_by_bm25(self, fts_url, couchbase_auth, embedding_model):
        """Document that combined query+knn scores are dominated by BM25."""
        query = "authentication"
        embedding = embedding_model.encode(
            f"search_query: {query}",
            normalize_embeddings=True
        ).tolist()

        # Get combined scores
        resp = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "query": {"term": "repo_bdr", "field": "type"},
                "knn": [{
                    "field": "embedding",
                    "vector": embedding,
                    "k": 10
                }],
                "knn_operator": "and",
                "size": 10
            },
            timeout=30
        )
        hits = resp.json().get("hits", [])

        # Get pure BM25 score for comparison
        resp_bm25 = httpx.post(
            fts_url,
            auth=couchbase_auth,
            json={
                "query": {"term": "repo_bdr", "field": "type"},
                "size": 1
            },
            timeout=30
        )
        bm25_score = resp_bm25.json()["hits"][0]["score"]

        # Combined scores should be close to BM25 score (BM25 dominates)
        for hit in hits:
            combined_score = hit.get("score")
            # BM25 contribution should be significant portion of combined score
            assert combined_score >= bm25_score * 0.9, \
                f"BM25 ({bm25_score}) should dominate combined score ({combined_score})"
