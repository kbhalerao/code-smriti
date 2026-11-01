#!/usr/bin/env python3
"""
Does the query vector the API serves land where the corpus expects it?

The corpus is built by the Hugging Face model in-process; the API embeds queries
with a q8_0 GGUF of the same weights over Ollama. A quantized build is a slightly
different function, and nothing raises if it drifts, so this measures the only
thing that matters: retrieval against the vectors actually stored in the corpus.

Each query is the lead sentence of a symbol's own summary, as in
benchmark_retrieval.py, and that symbol is the correct answer. Both arms use the
same stored vectors as the haystack and differ only in who embedded the query.

It also prints the probe anchor for `app/rag/embedding.py`, so the anchor can be
regenerated with the same model rather than copied from somewhere.

Re-run this before changing `ollama_model` in the api-server's convention.

Usage:
    ./.venv/bin/python scripts/measure_query_serving.py
    ./.venv/bin/python scripts/measure_query_serving.py --haystack 5000 --queries 300
"""

import argparse
import json
import random
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import numpy as np
from couchbase.options import QueryOptions
from sentence_transformers import SentenceTransformer

from embeddings.convention import EMBEDDING_DIMS, EMBEDDING_MODEL, truncate
from scripts.benchmark_retrieval import OLLAMA, query_styles
from storage.couchbase_client import CouchbaseClient

OLLAMA_TAG = "qwen3-embedding:0.6b-q8_0"
# Must equal `query_prefix` in services/api-server/app/rag/embedding.py.
QUERY_PREFIX = (
    "Instruct: Given a web search query, retrieve relevant passages "
    "that answer the query\nQuery:"
)
PROBE_TEXT = "probe"
ANCHOR_DIMS = 32


def ollama_embed(texts: list, batch: int = 32) -> np.ndarray:
    vecs = []
    with httpx.Client(timeout=300.0) as client:
        for i in range(0, len(texts), batch):
            r = client.post(f"{OLLAMA}/api/embed",
                            json={"model": OLLAMA_TAG, "input": texts[i:i + batch]})
            r.raise_for_status()
            vecs.extend(r.json()["embeddings"])
    return np.asarray(vecs, dtype=np.float32)


def ranks_of(queries: np.ndarray, docs: np.ndarray, targets: list) -> np.ndarray:
    order = np.argsort(-(queries @ docs.T), axis=1)
    return np.array([int(np.where(order[r] == t)[0][0]) + 1 for r, t in enumerate(targets)])


def describe(name: str, ranks: np.ndarray) -> None:
    print(f"  {name:<10} MRR {np.mean(1 / ranks):.4f}  R@1 {np.mean(ranks == 1):.4f}  "
          f"R@10 {np.mean(ranks <= 10):.4f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--haystack", type=int, default=5000)
    ap.add_argument("--queries", type=int, default=300)
    ap.add_argument("--seed", type=int, default=13)
    args = ap.parse_args()

    cb = CouchbaseClient()
    rows = list(cb.cluster.query(
        """
        SELECT d.content, d.embedding
        FROM `code_kosha` d
        WHERE d.type = 'symbol_index'
          AND d.quality.enrichment_level = 'llm_summary'
          AND d.embedding IS NOT MISSING
        LIMIT 40000
        """,
        QueryOptions(timeout=timedelta(minutes=10)),
    ))
    random.seed(args.seed)
    random.shuffle(rows)
    rows = [r for r in rows
            if len((r.get("content") or "").strip()) >= 60
            and len(r["embedding"]) == EMBEDDING_DIMS][:args.haystack]
    targets = random.sample(range(len(rows)), args.queries)
    print(f"haystack {len(rows)} stored vectors, {len(targets)} queries")

    docs = np.asarray([r["embedding"] for r in rows], dtype=np.float32)
    queries = [query_styles(rows[t]["content"])["lead"] for t in targets]

    model = SentenceTransformer(EMBEDDING_MODEL)
    hf_native = model.encode(queries, prompt_name="query", convert_to_numpy=True,
                             normalize_embeddings=True, show_progress_bar=False)
    hf = truncate(np.asarray(hf_native, dtype=np.float32))
    served = truncate(ollama_embed([f"{QUERY_PREFIX}{q}" for q in queries]))

    cosines = np.sum(hf * served, axis=1)
    print(f"query cosine, HF vs served: min {cosines.min():.4f}  "
          f"p01 {np.percentile(cosines, 1):.4f}  median {np.median(cosines):.4f}")

    hf_ranks, served_ranks = ranks_of(hf, docs, targets), ranks_of(served, docs, targets)
    describe("HF", hf_ranks)
    describe("served", served_ranks)

    broke = int(np.sum((hf_ranks == 1) & (served_ranks != 1)))
    rescued = int(np.sum((hf_ranks != 1) & (served_ranks == 1)))
    print(f"rank-1 hits: broke {broke}, rescued {rescued}; "
          f"rank unchanged on {int(np.sum(hf_ranks == served_ranks))}/{len(targets)}")
    top1_hf = np.argmax(hf @ docs.T, axis=1)
    top1_served = np.argmax(served @ docs.T, axis=1)
    print(f"same top-1 document: {int(np.sum(top1_hf == top1_served))}/{len(targets)}")

    anchor = np.asarray(
        model.encode(PROBE_TEXT, convert_to_numpy=True, normalize_embeddings=True),
        dtype=np.float32,
    )[:ANCHOR_DIMS]
    anchor = anchor / np.linalg.norm(anchor)
    live = ollama_embed([PROBE_TEXT])[0][:ANCHOR_DIMS]
    live = live / np.linalg.norm(live)
    print(f"probe anchor cosine, HF vs served ({ANCHOR_DIMS} dims): {float(anchor @ live):.4f}")
    print("probe_anchor =", json.dumps([round(float(x), 4) for x in anchor]))


if __name__ == "__main__":
    main()
