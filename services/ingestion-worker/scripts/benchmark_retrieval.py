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

`rerank` reorders the bi-encoder's top-K with a cross-encoder and re-scores the
same ranks, so the delta is attributable to the reranker alone. It reproduces the
semantics of app/rag/reranker.py exactly: the head is reordered, the tail is left
in bi-encoder order and appended, so a target that never entered the pool keeps
its original rank rather than being counted as lost.

The interesting knob is `--rerank-doc`, because the reranker does not necessarily
read the text the vector was built from:

    summary   the corpus `content` field alone — prose. This is what a reranker
              wired into search_code sees TODAY, since the pipeline embeds
              summary+code but persists only the summary.
    prod      summary + code, i.e. what the bi-encoder scored. Reranking on this
              requires fetching source for the top-K at query time.
    code      source alone — whether the cross-encoder bridges language to code
              unaided.

Result, 2026-08-21 — reranking the stored summaries makes retrieval WORSE.
qwen3-reranker-0.6b on :11435, against the configuration `search_code` ships
(K=20, scoring `content`, head reordered and tail appended):

    config                              MRR      R@1
    Qwen3-Embedding-0.6B alone         0.967    0.950
    + rerank K=20                      0.950    0.917
    + rerank K=20, query instruction   0.947    0.910
    + rerank K=50                      0.953    0.920
    + rerank K=20, keyword queries     0.941    0.907
    + rerank on summary+code           0.919    0.863
    + rerank on code alone             0.892    0.833

The last two answer the obvious objection — that the reranker only failed
because it reread the summary the vector was built from, and would do better on
source the bi-encoder never sees at query time. It does the opposite. The damage
scales monotonically with how much code enters the scored text (-0.033 summary,
-0.087 summary+code, -0.117 code), so a cross-encoder judging a
natural-language query against raw source is worse at it, not better. There is
no remaining variant worth trying.

Perfect reordering of the same pool scores 1.000, so the headroom existed and
the reranker spent it. It is a real regression, but **only the paired test shows
that**: unpaired, 0.950 vs 0.917 on n=300 gives p ~ 0.10 and reads as noise,
because 273 of 300 queries never move and inflate the variance. Paired —
McNemar broke 14 rank-1 hits against 4 rescued, p = 0.031; Wilcoxon on
reciprocal rank p = 0.036; bootstrap 95% CI on the R@1 delta [-0.060, -0.007].
That is why `rerank` persists per-query ranks: the aggregate deltas here are
~10 documents and cannot be judged from the means alone.

The likely cause is visible in `--rerank-doc`: the bi-encoder is already at
R@1 0.950, and `content` holds the summary the vector was built from, so the
cross-encoder rescores identical evidence with more ways to break a correct top
hit than to fix a wrong one. `--rerank-doc code` needs llama-server running with
`-ub 2048` (set on com.smriti.reranker 2026-08-21) or long documents 500.

Usage:
    ./.venv/bin/python scripts/benchmark_retrieval.py sample --haystack 5000 --queries 300
    ./.venv/bin/python scripts/benchmark_retrieval.py run --model nomic-embed-text
    ./.venv/bin/python scripts/benchmark_retrieval.py run --model qwen3-embedding:0.6b-q8_0 \
        --truncate-dims 768 --save-candidates 50
    ./.venv/bin/python scripts/benchmark_retrieval.py rerank --baseline-config prod/lead \
        --rerank-doc summary
    ./.venv/bin/python scripts/benchmark_retrieval.py report
"""

import argparse
import json
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
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
# The cross-encoder is a separate llama-server, not an ollama model — it answers
# /rerank, which ollama does not implement.
RERANKER = os.environ.get("RERANKER_URL", "http://localhost:11435")
OUT = Path(os.environ.get("RETBENCH_OUT", "/tmp/retbench"))
BUCKET = "code_kosha"

# Matches how the pipeline builds an embedding today (v4/pipeline.py), so `prod`
# measures the corpus as it is rather than an idealised version of it.
CODE_CHARS = 2000

# Qwen3-Embedding and Qwen3-Reranker share a family convention: an instruction on
# the query side only. Held in one place so the rerank A/B tests the same wording
# the retrieval side is measured with.
INSTRUCTION = (
    "Instruct: Given a description of what some code does, "
    "retrieve the code that does it\nQuery: "
)

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
        return INSTRUCTION + text
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
            "code": code[:args.code_chars],
        })

    queries = random.sample(range(len(haystack)), min(args.queries, len(haystack)))
    payload = {
        "haystack": haystack,
        "query_idx": queries,
        "seed": args.seed,
        "code_chars": args.code_chars,
    }
    (OUT / "sample.json").write_text(json.dumps(payload))
    langs = {}
    for h in haystack:
        langs[h["language"]] = langs.get(h["language"], 0) + 1
    logger.info(f"haystack {len(haystack)} docs, {len(queries)} queries, "
                f"{len(seen_repo)} repos")
    logger.info(f"languages: {dict(sorted(langs.items(), key=lambda kv: -kv[1])[:8])}")


def build_doc(h: dict, doc_mode: str, cap: int | None) -> str:
    """One haystack entry rendered as a document, per `doc_mode`.

    Shared by the embedding pass and the rerank pass so the two cannot drift into
    scoring subtly different text and calling the difference a result.
    """
    code = h["code"][:cap] if cap else h["code"]
    if doc_mode == "summary":
        return h["summary"]
    if doc_mode == "prod":
        return f"{h['summary']}\n\nCode:\n{code}"
    return code


def metrics_from_ranks(ranks: np.ndarray, dims, n_hay: int, n_q: int) -> dict:
    """Standard known-item metrics from 1-based ranks of the target document."""
    ranks = np.asarray(ranks, dtype=np.float64)
    return {
        "mrr": float(np.mean(1.0 / ranks)),
        "recall@1": float(np.mean(ranks <= 1)),
        "recall@5": float(np.mean(ranks <= 5)),
        "recall@10": float(np.mean(ranks <= 10)),
        "median_rank": float(np.median(ranks)),
        "dims": dims,
        "haystack": n_hay,
        "n_queries": n_q,
    }


def mrl_truncate(a: np.ndarray, dims: int | None) -> np.ndarray:
    """
    Matryoshka truncation: keep the leading `dims` components and renormalise.

    Applied to queries and documents alike — truncating only one side compares
    vectors that no longer live in the same space. Renormalising matters because
    a truncated vector is no longer unit length, and the scoring below is a dot
    product that assumes it is.
    """
    if not dims or dims >= a.shape[1]:
        return a
    t = a[:, :dims]
    n = np.linalg.norm(t, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return t / n


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
    candidates = {}

    # Queries are embedded once per style, not once per (style, doc_mode): the
    # query side does not depend on how documents are built, and re-embedding it
    # costs minutes on the larger models for identical vectors.
    query_vecs = {}
    for style in args.styles:
        qs = [query_styles(hay[i]["summary"])[style] for i in qidx]
        logger.info(f"[{model}] embedding {len(qs)} queries ({style})")
        query_vecs[style] = mrl_truncate(
            embed_all(model, [prefixed(model, q, is_query=True) for q in qs], args.batch),
            args.truncate_dims,
        )

    for doc_mode in args.doc_modes:
        docs = [build_doc(h, doc_mode, args.code_chars) for h in hay]
        logger.info(f"[{model}] embedding {len(docs)} documents ({doc_mode})")
        D = mrl_truncate(
            embed_all(model, [prefixed(model, d, is_query=False) for d in docs], args.batch),
            args.truncate_dims,
        )

        for style in args.styles:
            Q = query_vecs[style]

            sims = Q @ D.T                      # unit vectors, so this is cosine
            order = np.argsort(-sims, axis=1)
            ranks = np.array([
                int(np.where(order[row] == target)[0][0]) + 1
                for row, target in enumerate(qidx)
            ])
            results[f"{doc_mode}/{style}"] = metrics_from_ranks(
                ranks, int(D.shape[1]), len(hay), len(qidx)
            )
            if args.save_candidates:
                # The reranker needs the pool the bi-encoder produced, not just
                # the score it earned. Recomputing it later would mean a second
                # full embedding pass over the haystack.
                candidates[f"{doc_mode}/{style}"] = {
                    "pool": order[:, :args.save_candidates].tolist(),
                    "ranks": ranks.tolist(),
                }
            logger.info(f"  {doc_mode}/{style}: MRR {results[f'{doc_mode}/{style}']['mrr']:.3f} "
                        f"R@1 {results[f'{doc_mode}/{style}']['recall@1']:.3f} "
                        f"R@10 {results[f'{doc_mode}/{style}']['recall@10']:.3f}")

    suffix = f"_mrl{args.truncate_dims}" if args.truncate_dims else ""
    suffix += f"_cc{args.code_chars}" if args.code_chars else ""
    slug = model.replace(":", "_").replace("/", "_") + suffix
    path = OUT / f"result_{slug}.json"
    path.write_text(json.dumps({"model": model, "results": results}, indent=2))
    logger.info(f"wrote {path}")

    if args.save_candidates:
        cpath = OUT / f"candidates_{slug}.json"
        cpath.write_text(json.dumps({
            "model": model,
            "k": args.save_candidates,
            "code_chars": args.code_chars,
            "by_config": candidates,
        }))
        logger.info(f"wrote {cpath}")


def rerank_order(client: httpx.Client, query: str, docs: list, timeout: float) -> tuple:
    """Order `docs` best-first. Returns (indices into docs, model name).

    Each (query, document) pair must fit the server's PHYSICAL batch (`-ub`,
    default 512 tokens), not merely its context. A pool of short summaries is
    fine; one long document earns a 500 for the whole request. See the failure
    guard in cmd_rerank.
    """
    r = client.post(f"{RERANKER}/rerank",
                    json={"query": query, "documents": docs, "top_n": len(docs)},
                    timeout=timeout)
    r.raise_for_status()
    body = r.json()
    # Sort rather than trusting the response order. Qwen3-Reranker returns a
    # yes-token probability, which is tiny in absolute terms (1e-2 for a good
    # match, 1e-4 for a bad one) — fine for ordering, useless as a threshold.
    res = sorted(body["results"], key=lambda x: x["relevance_score"], reverse=True)
    return [x["index"] for x in res], body.get("model", "unknown")


def cmd_rerank(args):
    data = json.loads((OUT / "sample.json").read_text())
    hay, qidx = data["haystack"], data["query_idx"]

    payload = json.loads((OUT / args.candidates).read_text())
    if args.baseline_config not in payload["by_config"]:
        raise SystemExit(f"{args.baseline_config} not in {args.candidates}; "
                         f"have {sorted(payload['by_config'])}")
    base = payload["by_config"][args.baseline_config]
    pools = np.array(base["pool"])
    base_ranks = np.array(base["ranks"])
    style = args.baseline_config.split("/")[1]

    k = min(args.k, pools.shape[1])
    cap = args.code_chars if args.code_chars is not None else payload.get("code_chars")
    logger.info(f"reranking top-{k} of {args.baseline_config} "
                f"({len(qidx)} queries) on rerank-doc={args.rerank_doc}")

    def one(client, row: int) -> tuple:
        """(new 1-based rank of the target, reranker name or None, error or None)."""
        target = qidx[row]
        pool = pools[row][:k].tolist()
        q = query_styles(hay[target]["summary"])[style]
        if args.instruct:
            q = INSTRUCTION + q
        docs = [build_doc(hay[j], args.rerank_doc, cap) for j in pool]
        if args.max_doc_chars:
            docs = [d[:args.max_doc_chars] for d in docs]
        try:
            order, name = rerank_order(client, q, docs, args.timeout)
        except httpx.HTTPStatusError as e:
            detail = ""
            try:
                detail = e.response.json()["error"]["message"]
            except Exception:
                detail = e.response.text[:200]
            logger.warning(f"query {row} failed ({detail}); keeping bi-encoder rank")
            return int(base_ranks[row]), None, detail
        except Exception as e:
            logger.warning(f"query {row} failed ({e}); keeping bi-encoder rank")
            return int(base_ranks[row]), None, str(e)
        reordered = [pool[i] for i in order]
        # Same contract as app/rag/reranker.py: the head is reordered and the
        # tail appended untouched, so a target outside the pool keeps its rank.
        if target in reordered:
            return reordered.index(target) + 1, name, None
        return int(base_ranks[row]), name, None

    t0 = time.perf_counter()
    limits = httpx.Limits(max_connections=args.concurrency)
    with httpx.Client(limits=limits) as client:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool_exec:
            scored = list(pool_exec.map(lambda row: one(client, row), range(len(qidx))))
    elapsed = time.perf_counter() - t0

    new_ranks = np.array([r for r, _, _ in scored])
    failures = sum(1 for _, name, _ in scored if name is None)
    model_name = next((name for _, name, _ in scored if name), "unknown")

    # A run that mostly fell back to bi-encoder order is not a measurement of the
    # reranker, but its numbers look like one — so refuse to persist it. The
    # usual cause is a document exceeding the server's physical batch.
    rate = failures / max(1, len(qidx))
    if rate > args.max_failure_rate:
        reason = next((d for _, _, d in scored if d), "")
        logger.error(f"{failures}/{len(qidx)} queries failed ({rate:.0%}); "
                     f"refusing to write a result. First error: {reason}")
        if "batch size" in reason:
            logger.error("Each (query, document) pair must fit llama-server's "
                         "physical batch. Either restart it with a larger -ub "
                         "(and matching -b), or pass --max-doc-chars.")
        raise SystemExit(1)
    before = metrics_from_ranks(base_ranks, None, len(hay), len(qidx))
    after = metrics_from_ranks(new_ranks, None, len(hay), len(qidx))

    # Ceiling: what perfect reordering of this pool would score. A small gap
    # between `after` and this says the reranker is near the best any reordering
    # could do and the remaining loss is recall, not ranking.
    oracle = np.where(base_ranks <= k, 1, base_ranks)
    ceiling = metrics_from_ranks(oracle, None, len(hay), len(qidx))

    out = {
        "reranker": model_name,
        "embedder": payload["model"],
        "baseline_config": args.baseline_config,
        "rerank_doc": args.rerank_doc,
        "k": k,
        "instruct": bool(args.instruct),
        "concurrency": args.concurrency,
        "failures": failures,
        "ms_per_query": elapsed * 1000.0 / max(1, len(qidx)),
        "wall_seconds": elapsed,
        "baseline": before,
        "reranked": after,
        "pool_ceiling": ceiling,
        # Per-query ranks, so the aggregate deltas above can be tested rather
        # than eyeballed. A 3-point R@1 move on 300 queries is ~10 documents;
        # whether that is signal needs the paired outcomes, not the means.
        "ranks": {"baseline": base_ranks.tolist(), "reranked": new_ranks.tolist()},
    }
    slug = (f"{model_name.replace(':', '_').replace('/', '_')}"
            f"_{args.rerank_doc}_{args.baseline_config.replace('/', '-')}_k{k}"
            f"{'_instruct' if args.instruct else ''}")
    path = OUT / f"rerank_{slug}.json"
    path.write_text(json.dumps(out, indent=2))

    logger.info(f"  baseline  MRR {before['mrr']:.3f}  R@1 {before['recall@1']:.3f}  "
                f"R@10 {before['recall@10']:.3f}")
    logger.info(f"  reranked  MRR {after['mrr']:.3f}  R@1 {after['recall@1']:.3f}  "
                f"R@10 {after['recall@10']:.3f}")
    logger.info(f"  ceiling   MRR {ceiling['mrr']:.3f}  R@1 {ceiling['recall@1']:.3f}")
    logger.info(f"  delta     MRR {after['mrr'] - before['mrr']:+.3f}  "
                f"R@1 {after['recall@1'] - before['recall@1']:+.3f}")
    logger.info(f"  {out['ms_per_query']:.0f} ms/query at concurrency "
                f"{args.concurrency}, {failures} failures")
    logger.info(f"wrote {path}")


def cmd_report(args):
    rows = []
    for p in sorted(OUT.glob("result_*.json")):
        d = json.loads(p.read_text())
        for key, m in d["results"].items():
            rows.append((d["model"], key, m))
    if not rows:
        logger.info("no embedding results yet")
    if rows:
        keys = sorted({k for _, k, _ in rows})
        print(f"\n{'model':<28}{'config':<18}{'dims':>6}{'MRR':>8}{'R@1':>8}"
              f"{'R@10':>8}{'med rank':>10}")
        for key in keys:
            for model, k, m in rows:
                if k != key:
                    continue
                print(f"{model:<28}{k:<18}{m['dims']:>6}{m['mrr']:>8.3f}"
                      f"{m['recall@1']:>8.3f}{m['recall@10']:>8.3f}"
                      f"{m['median_rank']:>10.0f}")
            print()
        print(f"haystack {rows[0][2]['haystack']} docs, "
              f"{rows[0][2]['n_queries']} queries")

    rer = sorted(OUT.glob("rerank_*.json"))
    if not rer:
        return
    print(f"\n{'reranker':<22}{'baseline':<16}{'doc':<9}{'K':>4}{'ins':>5}"
          f"{'MRR':>8}{'R@1':>8}{'dMRR':>8}{'dR@1':>8}{'ceil R@1':>10}{'ms/q':>8}")
    for path in rer:
        d = json.loads(path.read_text())
        b, a, c = d["baseline"], d["reranked"], d["pool_ceiling"]
        print(f"{d['reranker']:<22}{d['baseline_config']:<16}{d['rerank_doc']:<9}"
              f"{d['k']:>4}{('y' if d['instruct'] else 'n'):>5}"
              f"{a['mrr']:>8.3f}{a['recall@1']:>8.3f}"
              f"{a['mrr'] - b['mrr']:>+8.3f}{a['recall@1'] - b['recall@1']:>+8.3f}"
              f"{c['recall@1']:>10.3f}{d['ms_per_query']:>8.0f}")
    print("\ndMRR/dR@1 are against the same bi-encoder run; ceil R@1 is what a "
          "perfect reordering of the same pool would score.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample")
    s.add_argument("--haystack", type=int, default=5000)
    s.add_argument("--queries", type=int, default=300)
    s.add_argument("--per-repo", type=int, default=60)
    s.add_argument("--seed", type=int, default=13)
    s.add_argument("--code-chars", type=int, default=CODE_CHARS,
                   help="how much source to keep per symbol when sampling")
    s.set_defaults(func=cmd_sample)

    r = sub.add_parser("run")
    r.add_argument("--model", required=True)
    r.add_argument("--batch", type=int, default=32)
    r.add_argument("--styles", nargs="+", default=["lead", "keywords"])
    r.add_argument("--doc-modes", nargs="+", default=["prod", "code"])
    r.add_argument("--code-chars", type=int, default=None,
                   help="slice each document's source to this many chars before embedding")
    r.add_argument("--truncate-dims", type=int, default=None,
                   help="Matryoshka-truncate embeddings to this many dims, then renormalise")
    r.add_argument("--save-candidates", type=int, default=0, metavar="K",
                   help="persist each query's top-K document indices for `rerank`")
    r.set_defaults(func=cmd_run)

    k = sub.add_parser("rerank")
    k.add_argument("--candidates", required=True,
                   help="candidates_*.json written by `run --save-candidates`")
    k.add_argument("--baseline-config", default="prod/lead",
                   help="which doc_mode/style pool to rerank, e.g. prod/lead")
    k.add_argument("--rerank-doc", default="summary",
                   choices=["summary", "prod", "code"],
                   help="text the cross-encoder scores; `summary` is what "
                        "production stores in `content`")
    k.add_argument("--k", type=int, default=20,
                   help="pool size, matching app/rag/reranker.py's max_candidates")
    k.add_argument("--code-chars", type=int, default=None,
                   help="override the sample's per-document source cap")
    k.add_argument("--instruct", action="store_true",
                   help="prepend the Qwen instruction to the query side")
    k.add_argument("--concurrency", type=int, default=4,
                   help="parallel requests; llama-server reports 4 slots")
    k.add_argument("--max-doc-chars", type=int, default=None,
                   help="truncate each document before sending; needed when a "
                        "pair would exceed llama-server's physical batch (-ub)")
    k.add_argument("--max-failure-rate", type=float, default=0.02,
                   help="abort without writing if more than this fraction of "
                        "queries fell back to bi-encoder order")
    k.add_argument("--timeout", type=float, default=300.0)
    k.set_defaults(func=cmd_rerank)

    p = sub.add_parser("report")
    p.set_defaults(func=cmd_report)

    args = ap.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
