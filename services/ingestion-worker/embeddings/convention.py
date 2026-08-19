"""
One definition of how this corpus is embedded.

Every vector in `code_vector_index` must come from the same model, the same
prefixing convention and the same dimensionality, or search silently returns
noise — a vector from one model and a vector from another still produce a dot
product, it just means nothing. Nothing raises. That is why the convention lives
in one module instead of as string literals at each call site: there were six of
those, across two services, and getting any one wrong is undetectable at runtime.

Model choice, measured 2026-08-19 on 5,000 documents and 300 natural-language
queries (`scripts/benchmark_retrieval.py`):

    nomic-embed-text     MRR 0.752   recall@1 0.667
    Qwen3-Embedding-0.6B MRR 0.886   recall@1 0.817

a 7.7-sigma difference. Larger Qwen variants add nothing measurable (4B 0.885,
8B 0.888 — under 1 sigma), so the smallest is chosen: it re-embeds the corpus in
about three hours where the 8B takes thirteen.

Truncation to 768 dimensions is Matryoshka, which this model is trained for, and
it is free: MRR 0.884 against 0.886 native, recall@1 identical at 0.817. Holding
768 means neither FTS index changes shape.

Prefixing is asymmetric and model-specific. nomic is trained with literal
`search_query:` / `search_document:` prefixes; Qwen3-Embedding takes an
instruction on the query side only and documents bare. This is not cosmetic —
nomic scored 0.729 AUC with its prefix and 0.615 without.
"""

import os
from typing import List, Optional, Sequence

import numpy as np
from loguru import logger

# The model every producer must agree on.
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-0.6B"

# Matryoshka target. The model emits 1024; the index stores 768.
EMBEDDING_DIMS = 768

# How much of a symbol's source reaches the embedding.
#
# Raised from 2,000 when the model's window went from nomic's 2,048 tokens to
# 32,768. Chosen by measurement rather than by the window: on 5,000 documents and
# 300 queries, 2,000 chars scored MRR 0.887 and 8,000 scored 0.890 — under half a
# standard error, so the gain is not established. It is taken anyway because it
# costs 56 minutes once (199 vs 143 for the corpus), discards 4.3% of code
# characters instead of 25.9%, and all four metrics moved in its favour; if that
# small effect is real it is captured, and if the cap were left low and later
# found to matter, the whole corpus would need re-embedding to change it.
#
# Beyond this the remainder is generated migrations, emscripten output and
# minified bundles, where a single vector is the wrong representation at any
# context size.
CODE_CHARS_FOR_EMBEDDING = 8000

# Per-model conventions. `query_prompt_name` uses the prompt the model ships in
# its sentence-transformers config; `*_prefix` is for models trained on literal
# string prefixes instead.
_CONVENTIONS = {
    "Qwen/Qwen3-Embedding-0.6B": {
        "query_prompt_name": "query",
        "query_prefix": "",
        "document_prefix": "",
        "native_dims": 1024,
    },
    "nomic-ai/nomic-embed-text-v1.5": {
        "query_prompt_name": None,
        "query_prefix": "search_query: ",
        "document_prefix": "search_document: ",
        "native_dims": 768,
    },
}


def convention(model_name: str = EMBEDDING_MODEL) -> dict:
    if model_name not in _CONVENTIONS:
        raise ValueError(
            f"No embedding convention for {model_name!r}. Add one rather than "
            f"guessing: the wrong prefix costs about 0.11 AUC and fails silently."
        )
    return _CONVENTIONS[model_name]


def truncate(vectors: np.ndarray, dims: int = EMBEDDING_DIMS) -> np.ndarray:
    """
    Matryoshka-truncate and renormalise.

    Renormalising is not optional: the index scores with dot_product, which is
    the cosine only for unit vectors, and a truncated vector is no longer unit
    length.
    """
    if vectors.ndim == 1:
        vectors = vectors[None, :]
    if dims >= vectors.shape[1]:
        out = vectors
    else:
        out = vectors[:, :dims]
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return out / norms


# Batching is bounded by two limits, not one.
#
# A single token budget assumes cost is linear in sequence length. It is not:
# the feed-forward work is linear but attention is quadratic, so a batch of
# sixteen 1,024-token documents and a batch of one 16,384-token document have
# the same token count and wildly different cost. Measured on this hardware,
# raising a linear budget from 16,384 to 65,536 tokens made throughput *worse*
# (4.5 -> 3.5 docs/s), which is what being past the memory-pressure knee looks
# like rather than being short of parallelism.
#
# So batches must satisfy both: total tokens, and total attention work.
MAX_TOKENS_PER_BATCH = int(os.environ.get("EMBED_MAX_TOKENS_PER_BATCH", 16384))
MAX_ATTENTION_PER_BATCH = int(os.environ.get("EMBED_MAX_ATTENTION_PER_BATCH", 24_000_000))
MAX_ITEMS_PER_BATCH = int(os.environ.get("EMBED_MAX_ITEMS_PER_BATCH", 64))

# Smallest batch worth retrying at before giving up on a document.
MIN_ITEMS_ON_RETRY = 1

# Roughly four characters per token for source code. Only used to group work, so
# an approximation is fine — the tokenizer still decides the real length.
_CHARS_PER_TOKEN = 4


def _budgeted_batches(texts: Sequence[str], max_seq_length: int = 4096) -> List[List[int]]:
    """
    Group indices so a batch stays inside both the token and attention budgets.

    Sorted longest-first, which does double duty: an oversized document lands in
    a small batch of its own rather than dragging a full batch up to its padded
    length, and neighbouring items have similar lengths so padding waste is low.
    """
    # Effective length, not character length. The model truncates at
    # max_seq_length, so a 200,000-character generated file costs exactly the
    # same as a 16,000-character one — estimating from characters made the
    # budget meaningless for precisely the documents it exists to contain.
    def tokens_of(i: int) -> int:
        return min(len(texts[i]) // _CHARS_PER_TOKEN + 1, max_seq_length)

    order = sorted(range(len(texts)), key=lambda i: -tokens_of(i))
    batches: List[List[int]] = []
    current: List[int] = []
    longest = 0

    def fits(candidate_len: int, count: int) -> bool:
        return (
            candidate_len * count <= MAX_TOKENS_PER_BATCH
            and candidate_len * candidate_len * count <= MAX_ATTENTION_PER_BATCH
            and count <= MAX_ITEMS_PER_BATCH
        )

    for i in order:
        tokens = tokens_of(i)
        candidate = max(longest, tokens)
        if current and not fits(candidate, len(current) + 1):
            batches.append(current)
            current, longest = [i], tokens
        else:
            current.append(i)
            longest = candidate
    if current:
        batches.append(current)
    return batches


def _encode_with_backoff(model, batch_texts: List[str], normalize: bool = True):
    """
    Encode a batch, halving it on an allocator failure rather than dying.

    A 180,000-document run that aborts on one pathological input has wasted
    however long it had been going. The batch sizer is an estimate — it works in
    characters, not real tokens — so it will occasionally be wrong, and being
    wrong should cost a retry rather than the run.
    """
    try:
        return model.encode(
            batch_texts,
            batch_size=len(batch_texts),
            convert_to_tensor=False,
            show_progress_bar=False,
            normalize_embeddings=normalize,
        )
    except RuntimeError as e:
        if len(batch_texts) <= MIN_ITEMS_ON_RETRY:
            raise
        logger.warning(
            f"batch of {len(batch_texts)} failed ({str(e)[:70]}); halving and retrying"
        )
        mid = len(batch_texts) // 2
        first = _encode_with_backoff(model, batch_texts[:mid], normalize)
        second = _encode_with_backoff(model, batch_texts[mid:], normalize)
        return np.concatenate([np.asarray(first), np.asarray(second)], axis=0)


def encode_documents(model, texts: Sequence[str], batch_size: int = 32) -> List[List[float]]:
    """
    Embed text that will be stored and searched against.

    `batch_size` is accepted for call-site compatibility but not used: batching
    is decided by token budget, which is the only thing that keeps long documents
    from exhausting memory.
    """
    conv = convention()
    prepared = [f"{conv['document_prefix']}{t}" for t in texts]
    out: List[List[float]] = [None] * len(prepared)  # type: ignore[list-item]
    max_seq = int(getattr(model, "max_seq_length", 4096) or 4096)
    for batch in _budgeted_batches(prepared, max_seq_length=max_seq):
        vecs = _encode_with_backoff(model, [prepared[i] for i in batch])
        for slot, vector in zip(batch, truncate(np.asarray(vecs, dtype=np.float32)).tolist()):
            out[slot] = vector
    return out


def encode_query(model, text: str) -> List[float]:
    """Embed a search query. Asymmetric with `encode_documents` by design."""
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


def manifest(document_count: Optional[int] = None) -> dict:
    """
    The record written to the corpus saying how it was embedded.

    Readers check this at startup rather than trusting configuration to have
    been changed in every service at once. A dimension check alone cannot catch
    this — truncating to 768 keeps the shape identical while changing the space.
    """
    return {
        "type": "embedding_manifest",
        "document_id": "embedding_manifest",
        "model": EMBEDDING_MODEL,
        "dims": EMBEDDING_DIMS,
        "native_dims": convention()["native_dims"],
        "code_chars": CODE_CHARS_FOR_EMBEDDING,
        "document_count": document_count,
    }
