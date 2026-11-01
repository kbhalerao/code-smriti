"""
How queries are embedded. Must agree with the ingestion worker exactly.

This mirrors `services/ingestion-worker/embeddings/convention.py`. The two
services cannot share code — separate images, separate dependency trees — so
the agreement is enforced at runtime instead, by `assert_corpus_matches()`
checking the manifest the worker writes into the corpus.

That check exists because the obvious guard does not work. A dimension check
passes while the space changes underneath it: the model emits 1024 dimensions
and the index stores 768 Matryoshka-truncated, which is the same shape nomic
produced and a completely different space. A vector from one model and a vector
from another still yield a dot product. It is a number, not a measurement, and
nothing raises.

Model chosen on measured retrieval over 5,000 documents and 300 queries:
nomic-embed-text MRR 0.752 / recall@1 0.667 against Qwen3-Embedding-0.6B
MRR 0.886 / recall@1 0.817, a 7.7-sigma difference. Larger Qwen variants add
nothing measurable. Truncating to 768 costs nothing (0.884 vs 0.886) and keeps
both FTS indexes their current shape.

Queries are embedded by Ollama over HTTP, not by an in-process model. Loading
Qwen3-Embedding-0.6B through sentence-transformers cost 2.7 GB resident in the
API container, and on an 8 GB Docker VM shared with Couchbase that was enough to
make the kernel OOM-kill the API in a restart loop. The served model is the
`q8_0` GGUF of the same weights, so agreement with the corpus (built from the
Hugging Face model) is measured rather than assumed: over 20 queries and 20
documents, 768-dim cosine to the Hugging Face vector was >= 0.9986 for queries
and >= 0.9993 for documents, and top-1 retrieval against Hugging Face document
vectors agreed on 20 of 20. The same test with the query instruction left off
fell to a 0.77 minimum cosine, so the instruction below is not optional.

Then measured against the corpus itself, by the ingestion worker's
`scripts/measure_query_serving.py` (300 queries, 5,000 stored vectors): served
queries score MRR 0.957 / R@1 0.940 against 0.959 / 0.943 for the Hugging Face
model. One rank-1 hit lost, none gained, minimum query cosine 0.9987. Re-run it
before changing the `ollama_model` tag.
"""

from functools import lru_cache
from typing import List, Optional

import httpx
import numpy as np

from app.http_client import get_http_client

EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_DIMS = 768

# `ollama_model` is the tag that serves the model's vectors; None means this
# service cannot embed queries for it. The tag is part of the space (quantization
# shifts vectors slightly), so it lives here rather than in settings. Unlike the
# ingestion worker's copy, the query instruction is the literal text: that
# worker lets sentence-transformers look the prompt up by name, and Ollama has no
# prompt registry. The text is the `query` prompt in the model's
# config_sentence_transformers.json, concatenated with no separator.
_CONVENTIONS = {
    "Qwen/Qwen3-Embedding-0.6B": {
        "query_prefix": (
            "Instruct: Given a web search query, retrieve relevant passages "
            "that answer the query\nQuery:"
        ),
        "native_dims": 1024,
        "ollama_model": "qwen3-embedding:0.6b-q8_0",
        # Leading dims of the Hugging Face model's vector for PROBE_TEXT, printed by
        # scripts/measure_query_serving.py. The served q8_0 scores 0.9998 against it.
        "probe_anchor": [
            -0.0104, -0.2361, -0.0516, -0.273, 0.2557, 0.0871, -0.0004, 0.2054,
            -0.1486, 0.2403, -0.0547, -0.1915, 0.1663, -0.0471, -0.2179, 0.3455,
            -0.2015, 0.0412, 0.2355, -0.104, -0.1337, 0.1058, -0.1356, 0.435,
            0.0934, 0.0374, -0.0167, 0.1976, -0.0907, -0.0549, 0.14, 0.1003,
        ],
    },
    "nomic-ai/nomic-embed-text-v1.5": {
        "query_prefix": "search_query: ",
        "native_dims": 768,
        "ollama_model": None,
        "probe_anchor": None,
    },
}

PROBE_TEXT = "probe"
# Unrelated vectors score near 0 here and the right model 0.9998, so this is not
# finely tuned. It catches the wrong model; it cannot see a small quantization
# shift, which is what measure_query_serving.py is for.
PROBE_MIN_COSINE = 0.99



def convention(model_name: str = EMBEDDING_MODEL) -> dict:
    if model_name not in _CONVENTIONS:
        raise ValueError(
            f"No embedding convention for {model_name!r}. Add one rather than "
            f"guessing: the wrong prefix cost about 0.11 AUC when measured, and "
            f"fails silently."
        )
    return _CONVENTIONS[model_name]


def truncate(vectors: np.ndarray, dims: int = EMBEDDING_DIMS) -> np.ndarray:
    """Matryoshka-truncate and renormalise — the index scores with dot_product,
    which is the cosine only for unit vectors."""
    if vectors.ndim == 1:
        vectors = vectors[None, :]
    out = vectors if dims >= vectors.shape[1] else vectors[:, :dims]
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return out / norms


class EmbeddingServiceError(RuntimeError):
    """The embedding host was unreachable or answered with something unusable."""


# What a client is told. The exception's own message names the internal host and
# model tag, so it belongs in the log and not in a response.
EMBEDDING_UNAVAILABLE_DETAIL = (
    "Search is temporarily unavailable: the embedding service did not answer."
)


class QueryEmbedder:
    """Embeds query text through Ollama's `/api/embed`."""

    def __init__(
        self,
        ollama_host: str,
        model_name: str = EMBEDDING_MODEL,
        timeout: float = 30.0,
        client: Optional[httpx.AsyncClient] = None,
    ):
        conv = convention(model_name)
        if not conv["ollama_model"]:
            raise ValueError(
                f"{model_name!r} has no Ollama tag in the embedding convention, "
                f"so queries cannot be embedded for it."
            )
        self.model_name = model_name
        self.ollama_model = conv["ollama_model"]
        self.native_dims = conv["native_dims"]
        self.url = f"{ollama_host.rstrip('/')}/api/embed"
        self.timeout = timeout
        self._client = client

    async def encode(self, text: str) -> np.ndarray:
        """The model's native-dimension vector for `text`, exactly as given.

        `keep_alive: -1` pins the 0.6B model (~0.6 GB) so a quiet hour does not
        turn the next search into a cold load.
        """
        try:
            resp = await (self._client or get_http_client()).post(
                self.url,
                json={"model": self.ollama_model, "input": text, "keep_alive": -1},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            vectors = resp.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise EmbeddingServiceError(
                f"Embedding request to {self.url} ({self.ollama_model}) failed: "
                f"{type(e).__name__}: {e}"
            ) from e

        # A model that is not the one we asked for still returns a vector. Its
        # width is the only thing this boundary can check cheaply.
        if len(vectors) != 1 or len(vectors[0]) != self.native_dims:
            got = len(vectors[0]) if vectors else 0
            raise EmbeddingServiceError(
                f"{self.ollama_model} returned {len(vectors)} vector(s) of {got} "
                f"dimensions, expected 1 of {self.native_dims}."
            )
        return np.asarray(vectors[0], dtype=np.float32)


@lru_cache(maxsize=None)
def get_embedding_model(model_name: str = EMBEDDING_MODEL) -> QueryEmbedder:
    """The query embedder for `model_name`, pointed at the configured Ollama host."""
    from app.config import settings

    try:
        return QueryEmbedder(settings.ollama_host, model_name)
    except ValueError as e:
        # Misconfiguration, known only once a request needs the embedder. It has
        # the same effect as the host being down, and takes the same 503 rather
        # than a bare ValueError from whichever route got here first.
        raise EmbeddingServiceError(str(e)) from e


async def embed_query(embedder: QueryEmbedder, text: str) -> List[float]:
    """
    Embed a search query.

    Asymmetric with the document side on purpose: this model takes an
    instruction on queries and documents bare, where nomic took literal
    prefixes on both.
    """
    conv = convention(embedder.model_name)
    vec = await embedder.encode(f"{conv['query_prefix']}{text}")
    return truncate(vec)[0].tolist()


async def assert_corpus_matches(
    db, bucket: str = "code_kosha", loaded_model_name: Optional[str] = None,
) -> Optional[str]:
    """
    Compare this service's convention against how the corpus was actually built.

    Returns a description of the mismatch, or None only if agreement was actually
    established. Logged loudly rather than raised: a mismatch means results are
    wrong but the service is otherwise healthy, and refusing to start would take
    down search entirely when degraded search plus a klaxon is more useful.
    """
    # Comparing this module's constants against the manifest proves only that two
    # pieces of configuration agree. It said "matches" while the service had
    # nomic loaded, because EMBEDDING_MODEL_NAME in .env overrode the compose
    # default and nothing checked what was actually in memory. Checked first, as
    # it needs no database and should not be hidden by a database problem.
    if loaded_model_name and loaded_model_name != EMBEDDING_MODEL:
        return (
            f"Configured model is {loaded_model_name}, but this service embeds "
            f"queries as {EMBEDDING_MODEL}. Check EMBEDDING_MODEL_NAME — a "
            f"value in .env overrides the docker-compose default."
        )

    try:
        doc = await db.get_doc(bucket, "embedding_manifest")
    except Exception as e:
        return f"Could not read the embedding manifest, so the corpus is unverified: {e}"
    if doc is None:
        return (
            "No embedding manifest in the corpus. It predates the manifest, so "
            "which model produced its vectors cannot be verified from here."
        )
    if doc.get("model") != EMBEDDING_MODEL or doc.get("dims") != EMBEDDING_DIMS:
        return (
            f"Embedding mismatch: corpus built with {doc.get('model')} at "
            f"{doc.get('dims')} dims, this service is configured for "
            f"{EMBEDDING_MODEL} at {EMBEDDING_DIMS}. Search results are "
            f"meaningless until one side is re-run."
        )
    return None


async def probe_embedder(model_name: str = EMBEDDING_MODEL) -> Optional[str]:
    """
    Embed something through the path a search uses. Returns the problem, or None.

    Independent of the manifest check on purpose: a corpus that cannot be read says
    nothing about whether the embedding host is up, and each must be able to report
    without the other having passed.

    `encode` rejects the wrong width, but any model of that width passes that and
    any tag can be repointed, so the vector is also compared to an anchor taken
    from the model the corpus was built with.
    """
    try:
        vec = await get_embedding_model(model_name).encode(PROBE_TEXT)
    except EmbeddingServiceError as e:
        return (
            f"Could not embed a probe query, so searches will fail until this is "
            f"fixed: {e}"
        )
    if truncate(vec).shape[1] != EMBEDDING_DIMS:
        return (
            f"Served model emits {truncate(vec).shape[1]} dimensions after "
            f"truncation, expected {EMBEDDING_DIMS}."
        )
    anchor = np.asarray(convention(model_name)["probe_anchor"], dtype=np.float32)
    leading = vec[: anchor.size]
    cosine = float(leading @ anchor / (np.linalg.norm(leading) * np.linalg.norm(anchor)))
    if cosine < PROBE_MIN_COSINE:
        return (
            f"The served model is not in the corpus's space: probe cosine {cosine:.3f} "
            f"against the reference, expected at least {PROBE_MIN_COSINE}. Check what "
            f"{get_embedding_model(model_name).ollama_model} points at in Ollama."
        )
    return None
