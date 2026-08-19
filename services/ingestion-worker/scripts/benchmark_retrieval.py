#!/usr/bin/env python3
"""
Can a model find the right code from a natural-language query?

The clone benchmark measured structure discrimination and answered "fingerprints,
not embeddings". It did not measure what smriti actually does all day, which is
retrieval: someone describes what they want and the index returns code. This
measures that, and it is the axis a migration decision should rest on.

Queries are derived from summaries rather than being them. The stored document is
`summary + code`, so feeding back the whole summary retrieves by literal string
overlap and every model scores near-perfectly — the same failure the clone
benchmark had when 74.5% of tokens survived renaming. Two derived styles instead:

    lead      the summary's first sentence — how someone actually asks
    keywords  content words only, order preserved — how a search box gets used

`full` is available as an upper bound, but it measures matching, not retrieval.

Two document configurations, because they answer different questions:

    prod   summary + code[:2000], exactly as the corpus stores it. This is the
           production question: how well does retrieval work today?
    code   code alone. This asks whether the model bridges language to code
           without the summary carrying it — which is what happens for every
           symbol whose summary is poor, and what a drafting model would need.

Prefixing is asymmetric here, and differs from the clone benchmark on purpose.
That task was symmetric similarity, so Qwen correctly ran with no instruction.
Retrieval is asymmetric: nomic wants `search_query:` / `search_document:`, and
Qwen3-Embedding wants its instruction prefix on the query side only. Both get the
form they were trained for; scoring either in the other's convention would decide
the benchmark by a formatting choice.

Usage:
    ./.venv/bin/python scripts/benchmark_retrieval.py sample --haystack 5000 --queries 300
    ./.venv/bin/python scripts/benchmark_retrieval.py run --model nomic-embed-text
    ./.venv/bin/python scripts/benchmark_retrieval.py report
"""

import argparse
import json
import os
import random
import re
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import numpy as np
from couchbase.options import QueryOptions
from loguru import logger

from config import WorkerConfig
from storage.couchbase_client import CouchbaseClient

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OUT = Path(os.environ.get("RETBENCH_OUT", "/tmp/retbench"))
BUCKET = "code_kosha"

# Matches how the pipeline builds an embedding today (v4/pipeline.py), so `prod`
# measures the corpus as it is rather than an idealised version of it.
CODE_CHARS = 2000

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "how", "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "then",
    "this", "to", "up", "was", "which", "will", "with", "if", "when", "any", "each",
    "function", "method", "class", "code", "used", "uses", "using", "given", "also",
}


def query_styles(summary: str) -> dict:
    """Three ways of asking for the same thing, from easiest to most realistic."""
    lead = re.split(r"(?<=[.!?])\s+", summary.strip())[0]
    words = [w for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]+", summary.lower())
             if w not in STOPWORDS and len(w) > 2]
    return {
        "full": summary.strip(),
        "lead": lead,
        "keywords": " ".join(words[:18]),
    }


def prefixed(model: str, text: str, is_query: bool) -> str:
    """
    Each model in the convention it was trained for.

    nomic is a bi-encoder with explicit task prefixes. Qwen3-Embedding takes an
    instruction on the query side only; instructing the document side degrades it.
    """
    if model.startswith("nomic"):
        return f"{'search_query' if is_query else 'search_document'}: {text}"
    if model.startswith("qwen3-embedding") and is_query:
        return (
            "Instruct: Given a description of what some code does, "
            "retrieve the code that does it\nQuery: " + text
        )
    return text


def cmd_sample(args):
    OUT.mkdir(parents=True, exist_ok=True)
    cb = CouchbaseClient()
    repos = Path(WorkerConfig().repos_path)

    rows = list(cb.cluster.query(
        f"""
        SELECT d.document_id, d.repo_id, d.file_path, d.content,
               d.`language` AS lang,
               d.metadata.start_line AS s, d.metadata.end_line AS e
        FROM `{BUCKET}` d
        WHERE d.type = 'symbol_index'
          AND d.quality.enrichment_level = 'llm_summary'
          AND d.metadata.start_line IS NOT MISSING
        LIMIT 60000
        """,
        QueryOptions(timeout=timedelta(minutes=10)),
    ))
    logger.info(f"candidate pool: {len(rows)}")

    random.seed(args.seed)
    random.shuffle(rows)

    haystack, seen_repo = [], {}
    for r in rows:
        if len(haystack) >= args.haystack:
            break
        summary = (r.get("content") or "").strip()
        if len(summary) < 60:
            continue
        # Cap per repo so one large repository cannot dominate the haystack and
        # turn this into a measurement of that repo's idiom.
        cap = seen_repo.get(r["repo_id"], 0)
        if cap >= args.per_repo:
            continue
        path = repos / r["repo_id"].replace("/", "_") / r["file_path"]
        if not path.is_file():
            continue
        try:
            lines = path.read_text(errors="ignore").split("\n")
        except Exception:
            continue
        code = "\n".join(lines[max(0, (r["s"] or 1) - 1):(r["e"] or 0)])
        if len(code) < 80:
            continue
        seen_repo[r["repo_id"]] = cap + 1
        haystack.append({
            "document_id": r["document_id"],
            "repo_id": r["repo_id"],
            "file_path": r["file_path"],
            "language": r.get("lang"),
            "summary": summary,
            "code": code[:CODE_CHARS],
        })

    queries = random.sample(range(len(haystack)), min(args.queries, len(haystack)))
    payload = {
        "haystack": haystack,
        "query_idx": queries,
        "seed": args.seed,
        "code_chars": CODE_CHARS,
    }
    (OUT / "sample.json").write_text(json.dumps(payload))
    langs = {}
    for h in haystack:
        langs[h["language"]] = langs.get(h["language"], 0) + 1
    logger.info(f"haystack {len(haystack)} docs, {len(queries)} queries, "
                f"{len(seen_repo)} repos")
    logger.info(f"languages: {dict(sorted(langs.items(), key=lambda kv: -kv[1])[:8])}")


def embed_all(model: str, texts: list, batch: int) -> np.ndarray:
    vecs = []
    with httpx.Client() as client:
        for i in range(0, len(texts), batch):
            r = client.post(f"{OLLAMA}/api/embed",
                            json={"model": model, "input": texts[i:i + batch],
                                  "keep_alive": "10m"},
                            timeout=900.0)
            r.raise_for_status()
            vecs.extend(r.json()["embeddings"])
            if (i // batch) % 10 == 0:
                logger.info(f"    {min(i + batch, len(texts))}/{len(texts)}")
    a = np.asarray(vecs, dtype=np.float32)
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return a / n


def cmd_run(args):
    data = json.loads((OUT / "sample.json").read_text())
    hay, qidx = data["haystack"], data["query_idx"]
    model = args.model

    results = {}

    # Queries are embedded once per style, not once per (style, doc_mode): the
    # query side does not depend on how documents are built, and re-embedding it
    # costs minutes on the larger models for identical vectors.
    query_vecs = {}
    for style in args.styles:
        qs = [query_styles(hay[i]["summary"])[style] for i in qidx]
        logger.info(f"[{model}] embedding {len(qs)} queries ({style})")
        query_vecs[style] = embed_all(
            model, [prefixed(model, q, is_query=True) for q in qs], args.batch
        )

    for doc_mode in args.doc_modes:
        docs = [
            (f"{h['summary']}\n\nCode:\n{h['code']}" if doc_mode == "prod" else h["code"])
            for h in hay
        ]
        logger.info(f"[{model}] embedding {len(docs)} documents ({doc_mode})")
        D = embed_all(model, [prefixed(model, d, is_query=False) for d in docs], args.batch)

        for style in args.styles:
            Q = query_vecs[style]

            sims = Q @ D.T                      # unit vectors, so this is cosine
            order = np.argsort(-sims, axis=1)
            ranks = np.array([
                int(np.where(order[row] == target)[0][0]) + 1
                for row, target in enumerate(qidx)
            ])
            results[f"{doc_mode}/{style}"] = {
                "mrr": float(np.mean(1.0 / ranks)),
                "recall@1": float(np.mean(ranks <= 1)),
                "recall@5": float(np.mean(ranks <= 5)),
                "recall@10": float(np.mean(ranks <= 10)),
                "median_rank": float(np.median(ranks)),
                "dims": int(D.shape[1]),
                "haystack": len(hay),
                "n_queries": len(qidx),
            }
            logger.info(f"  {doc_mode}/{style}: MRR {results[f'{doc_mode}/{style}']['mrr']:.3f} "
                        f"R@1 {results[f'{doc_mode}/{style}']['recall@1']:.3f} "
                        f"R@10 {results[f'{doc_mode}/{style}']['recall@10']:.3f}")

    path = OUT / f"result_{model.replace(':', '_').replace('/', '_')}.json"
    path.write_text(json.dumps({"model": model, "results": results}, indent=2))
    logger.info(f"wrote {path}")


def cmd_report(args):
    rows = []
    for p in sorted(OUT.glob("result_*.json")):
        d = json.loads(p.read_text())
        for key, m in d["results"].items():
            rows.append((d["model"], key, m))
    if not rows:
        logger.info("no results yet")
        return
    keys = sorted({k for _, k, _ in rows})
    print(f"\n{'model':<28}{'config':<18}{'dims':>6}{'MRR':>8}{'R@1':>8}{'R@10':>8}{'med rank':>10}")
    for key in keys:
        for model, k, m in rows:
            if k != key:
                continue
            print(f"{model:<28}{k:<18}{m['dims']:>6}{m['mrr']:>8.3f}"
                  f"{m['recall@1']:>8.3f}{m['recall@10']:>8.3f}{m['median_rank']:>10.0f}")
        print()
    print(f"haystack {rows[0][2]['haystack']} docs, {rows[0][2]['n_queries']} queries")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample")
    s.add_argument("--haystack", type=int, default=5000)
    s.add_argument("--queries", type=int, default=300)
    s.add_argument("--per-repo", type=int, default=60)
    s.add_argument("--seed", type=int, default=13)
    s.set_defaults(func=cmd_sample)

    r = sub.add_parser("run")
    r.add_argument("--model", required=True)
    r.add_argument("--batch", type=int, default=32)
    r.add_argument("--styles", nargs="+", default=["lead", "keywords"])
    r.add_argument("--doc-modes", nargs="+", default=["prod", "code"])
    r.set_defaults(func=cmd_run)

    p = sub.add_parser("report")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
