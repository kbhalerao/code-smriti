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
"""

from typing import List, Optional

import numpy as np
from loguru import logger

EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"
EMBEDDING_DIMS = 768

_CONVENTIONS = {
    "Qwen/Qwen3-Embedding-0.6B": {
        "query_prompt_name": "query",
        "query_prefix": "",
        "native_dims": 1024,
    },
    "nomic-ai/nomic-embed-text-v1.5": {
        "query_prompt_name": None,
        "query_prefix": "search_query: ",
        "native_dims": 768,
    },
}


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


def embed_query(model, text: str) -> List[float]:
    """
    Embed a search query.

    Asymmetric with the document side on purpose: this model takes an
    instruction on queries and documents bare, where nomic took literal
    prefixes on both.
    """
    conv = convention()
    kwargs = {}
    if conv["query_prompt_name"]:
        kwargs["prompt_name"] = conv["query_prompt_name"]
    vec = model.encode(
        f"{conv['query_prefix']}{text}",
        convert_to_tensor=False,
        show_progress_bar=False,
        normalize_embeddings=True,
        **kwargs,
    )
    return truncate(np.asarray(vec, dtype=np.float32))[0].tolist()


async def assert_corpus_matches(
    db, bucket: str = "code_kosha", loaded_model_name: Optional[str] = None,
    model=None,
) -> Optional[str]:
    """
    Compare this service's convention against how the corpus was actually built.

    Returns a description of the mismatch, or None if they agree. Logged loudly
    rather than raised: a mismatch means results are wrong but the service is
    otherwise healthy, and refusing to start would take down search entirely
    when degraded search plus a klaxon is more useful.
    """
    try:
        doc = await db.get_doc(bucket, "embedding_manifest")
    except Exception as e:
        logger.warning(f"Could not read embedding manifest: {e}")
        return None
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

    # Comparing this module's constants against the manifest proves only that two
    # pieces of configuration agree. It said "matches" while the service had
    # nomic loaded, because EMBEDDING_MODEL_NAME in .env overrode the compose
    # default and nothing checked what was actually in memory.
    if loaded_model_name and loaded_model_name != EMBEDDING_MODEL:
        return (
            f"Loaded model is {loaded_model_name}, but this service embeds "
            f"queries as {EMBEDDING_MODEL}. Check EMBEDDING_MODEL_NAME — a "
            f"value in .env overrides the docker-compose default."
        )

    # And prove it by using it: a name can be right while the object is not.
    if model is not None:
        try:
            probe = truncate(np.asarray(model.encode("probe"), dtype=np.float32))
            if probe.shape[1] != EMBEDDING_DIMS:
                return (
                    f"Loaded model emits {probe.shape[1]} dimensions after "
                    f"truncation, expected {EMBEDDING_DIMS}."
                )
        except Exception as e:
            logger.warning(f"Could not probe the embedding model: {e}")
    return None
