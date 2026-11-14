"""
An Ollama-served embedding model behind the slice of the SentenceTransformer
interface that `embeddings.convention` uses: `encode()` and `max_seq_length`.

It exists so the model can be served by Ollama without touching any caller.
Every document vector already goes through `convention.encode_documents`, which
does the prefixing, the batch sizing, the backoff and the Matryoshka truncation,
and hands this object one batch at a time.

Why Ollama rather than in-process: EmbeddingGemma 2 is built on the Gemma 4
architecture, which the pinned transformers (4.57) cannot load. And with both
sides served by the same tag, queries and documents come from the same build,
so the Hugging Face-versus-served drift that `measure_query_serving.py` had to
measure for Qwen does not arise.

Batching: one `/api/embed` request carries the whole batch. On this machine
throughput is GPU-bound at about 48 docs/s regardless of batch size (1 to 64)
or concurrency (1 to 4), measured 2026-11-14, so batching saves only round
trips and requests are sent one at a time.
"""

from typing import List, Sequence, Union

import httpx
import numpy as np

# Ollama answers a whole batch in one response, and a long-document batch can
# take a while on a busy server. The worker has nothing better to do than wait.
_TIMEOUT = 900.0


class OllamaEmbeddingModel:
    def __init__(self, base_url: str, tag: str, max_seq_length: int, native_dims: int):
        self.url = f"{base_url.rstrip('/')}/api/embed"
        self.tag = tag
        # Read by convention's batch sizer. Ollama truncates past the model's
        # context itself; this is the estimate the sizer groups work by.
        self.max_seq_length = max_seq_length
        self.native_dims = native_dims
        self.device = f"ollama:{tag}"
        self._client = httpx.Client(timeout=_TIMEOUT)

    def encode(
        self,
        sentences: Union[str, Sequence[str]],
        batch_size: int = 32,
        convert_to_tensor: bool = False,
        show_progress_bar: bool = False,
        normalize_embeddings: bool = True,
        **_ignored,
    ) -> np.ndarray:
        """
        Embed `sentences` exactly as given. Prefixes are the caller's job.

        Failures raise RuntimeError, which is what `_encode_with_backoff` halves
        a batch on, so one bad batch costs a retry rather than the run.
        """
        single = isinstance(sentences, str)
        texts: List[str] = [sentences] if single else list(sentences)
        try:
            resp = self._client.post(
                self.url,
                json={"model": self.tag, "input": texts, "truncate": True},
            )
            resp.raise_for_status()
            vectors = resp.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise RuntimeError(f"Ollama embed via {self.tag} failed: {type(e).__name__}: {e}") from e

        if len(vectors) != len(texts) or any(len(v) != self.native_dims for v in vectors):
            widths = sorted({len(v) for v in vectors})
            raise RuntimeError(
                f"{self.tag} returned {len(vectors)} vector(s) of width {widths} for "
                f"{len(texts)} input(s); expected width {self.native_dims}"
            )

        out = np.asarray(vectors, dtype=np.float32)
        if normalize_embeddings:
            norms = np.linalg.norm(out, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            out = out / norms
        return out[0] if single else out
