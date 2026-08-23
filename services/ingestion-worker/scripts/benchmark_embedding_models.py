#!/usr/bin/env python
"""
Benchmark embedding models on CODE-STRUCTURE discrimination.

Task: separate type-2 clones (identical AST, every identifier alpha-renamed to
meaningless names) from random unrelated function pairs. An embedding that
encodes code structure keeps the clone pair close; an embedding that only
encodes identifiers and topic collapses toward the random baseline.

Subcommands
-----------
  sample   harvest Python functions from the on-disk repo clones and build
           original / alpha-renamed twin pairs.  Writes sample.json.
  embed    embed one variant of the sample with one model, write vectors.
  report   score every embedded model and emit the markdown table.
  summary  secondary benchmark: symbol-summary (natural language) separation.

Run from services/ingestion-worker with ./.venv/bin/python.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import json
import keyword
import os
import random
import textwrap
import time
from pathlib import Path

import numpy as np
import httpx

REPOS_PATH = Path(os.environ.get("REPOS_PATH", "/Users/kaustubh/code/codesmriti-repos"))
OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OUT = Path(os.environ.get("EMBBENCH_OUT", "/tmp/embbench"))

# Identifiers that must never be renamed: renaming them changes the program's
# structure, not just its vocabulary.
RESERVED = set(keyword.kwlist) | set(keyword.softkwlist) | set(dir(builtins))
RESERVED |= {"self", "cls"}  # binding convention, not vocabulary

SKIP_DIR_PARTS = {
    ".git", "node_modules", "venv", ".venv", "site-packages", "__pycache__",
    "migrations", "build", "dist", ".tox", "vendor", "third_party",
}

MIN_LINES = 8
MAX_LINES = 60


# --------------------------------------------------------------------------
# alpha-renaming
# --------------------------------------------------------------------------

class Renamer(ast.NodeTransformer):
    """Rename every bound identifier to a meaningless name.

    Preserved: keywords, soft keywords, builtins, self/cls, attribute names
    (``x.append`` keeps ``append``), call keyword-argument names (``f(timeout=1)``
    keeps ``timeout``) -- those are library interface, not local vocabulary.
    Renamed: function/class names, arguments, locals, globals referenced by
    ``Name``, import aliases, except-handler names.
    """

    def __init__(self, strict: bool = False):
        self.map: dict[str, str] = {}
        self.attr_map: dict[str, str] = {}
        self.str_map: dict[str, str] = {}
        self.n_var = 0
        self.n_fn = 0
        # strict mode additionally renames attribute names, call keyword-argument
        # names and string literals, leaving nothing but keywords, builtins,
        # numbers and structure.  This is the true type-2 clone condition.
        self.strict = strict

    def _new(self, name: str, is_callable: bool) -> str:
        if name in RESERVED:
            return name
        if name in self.map:
            return self.map[name]
        if is_callable:
            self.n_fn += 1
            new = f"f{self.n_fn}"
        else:
            self.n_var += 1
            new = f"v{self.n_var}"
        self.map[name] = new
        return new

    # -- definitions -------------------------------------------------------
    def _visit_def(self, node, is_callable):
        node.name = self._new(node.name, is_callable)
        self.generic_visit(node)
        return node

    def visit_FunctionDef(self, node):
        return self._visit_def(node, True)

    def visit_AsyncFunctionDef(self, node):
        return self._visit_def(node, True)

    def visit_ClassDef(self, node):
        return self._visit_def(node, True)

    def visit_arg(self, node):
        node.arg = self._new(node.arg, False)
        self.generic_visit(node)
        return node

    def visit_Name(self, node):
        node.id = self._new(node.id, False)
        return node

    def visit_ExceptHandler(self, node):
        if node.name:
            node.name = self._new(node.name, False)
        self.generic_visit(node)
        return node

    def visit_Global(self, node):
        node.names = [self._new(n, False) for n in node.names]
        return node

    def visit_Nonlocal(self, node):
        node.names = [self._new(n, False) for n in node.names]
        return node

    def visit_alias(self, node):
        # `import numpy as np` -> the alias is a local name; the dotted module
        # path is API surface and stays.  `import numpy` binds `numpy` itself,
        # which we cannot rename without an alias, so leave un-aliased imports.
        if node.asname:
            node.asname = self._new(node.asname, False)
        return node

    # -- strict-mode-only vocabulary ---------------------------------------
    def visit_Attribute(self, node):
        self.generic_visit(node)
        if self.strict:
            node.attr = self.attr_map.setdefault(
                node.attr, f"a{len(self.attr_map) + 1}")
        return node

    def visit_keyword(self, node):
        self.generic_visit(node)
        if self.strict and node.arg:
            node.arg = self.attr_map.setdefault(
                node.arg, f"a{len(self.attr_map) + 1}")
        return node

    def visit_Constant(self, node):
        if self.strict and isinstance(node.value, str):
            node.value = self.str_map.setdefault(
                node.value, f"s{len(self.str_map) + 1}")
        return node


def strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove docstrings from every def/class/module in the tree.

    Docstrings are natural-language prose.  A clone twin keeps its original's
    docstring verbatim, so leaving them in measures prose identity rather than
    code structure -- exactly the confound this benchmark exists to isolate.
    """
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Module)):
            continue
        body = node.body
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return tree


def identifiers_of(tree: ast.AST) -> set[str]:
    ids = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            ids.add(n.id)
        elif isinstance(n, ast.arg):
            ids.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ids.add(n.name)
    return ids - RESERVED


def _rename_to_source(src: str, strict: bool) -> tuple[str, str]:
    """Alpha-rename `src` and return (with_docstring, without_docstring)."""
    renamed = Renamer(strict=strict).visit(ast.parse(src))
    ast.fix_missing_locations(renamed)
    with_doc = ast.unparse(renamed)
    without = ast.unparse(strip_docstrings(ast.parse(with_doc)))
    return with_doc, without


def make_pair(src: str) -> dict | None:
    """Build the original and its alpha-renamed twins, or None if unusable.

    Both sides go through ast.unparse so formatting and comments are on equal
    footing -- otherwise the benchmark would partly measure comment identity.
    Two renaming strictness levels are produced:

      plain  -- identifiers only; attribute names, call kwarg names and string
                literals survive (this is the brief's literal definition).
      strict -- those survive too, leaving only keywords, builtins, numbers and
                control-flow structure.
    """
    src = textwrap.dedent(src)
    try:
        ast.parse(src)
    except (SyntaxError, ValueError, RecursionError):
        return None

    try:
        orig_doc = ast.unparse(ast.parse(src))
        orig = ast.unparse(strip_docstrings(ast.parse(orig_doc)))
        twin_doc, twin = _rename_to_source(src, strict=False)
        twin_strict_doc, twin_strict = _rename_to_source(src, strict=True)
    except (SyntaxError, ValueError, RecursionError, AttributeError):
        return None

    # The twin must be valid Python and must actually have different vocabulary.
    try:
        o_ids = identifiers_of(ast.parse(orig))
        t_ids = identifiers_of(ast.parse(twin))
        ts_ids = identifiers_of(ast.parse(twin_strict))
    except (SyntaxError, ValueError, RecursionError):
        return None
    # A usable pair must (a) have had identifiers to strip, and (b) share none
    # of them with its twin -- any overlap means an identifier survived.
    if not o_ids or (t_ids & o_ids) or (ts_ids & o_ids):
        return None
    return {"orig": orig, "twin": twin, "twin_strict": twin_strict,
            "orig_doc": orig_doc, "twin_doc": twin_doc,
            "twin_strict_doc": twin_strict_doc}


# --------------------------------------------------------------------------
# sampling
# --------------------------------------------------------------------------

def iter_py_files(repo: Path, limit: int):
    n = 0
    for p in repo.rglob("*.py"):
        if any(part in SKIP_DIR_PARTS for part in p.parts):
            continue
        yield p
        n += 1
        if n >= limit:
            return


def cmd_sample(args):
    rng = random.Random(args.seed)
    repos = sorted(d for d in REPOS_PATH.iterdir() if d.is_dir())
    rng.shuffle(repos)

    stats = {
        "files_read": 0, "funcs_seen": 0,
        "discard_len": 0, "discard_parse": 0, "discard_rename": 0,
        "discard_dup": 0,
    }
    samples = []
    seen_bodies: set[int] = set()

    for repo in repos:
        if len(samples) >= args.n:
            break
        picked_here = 0
        files = list(iter_py_files(repo, 400))
        rng.shuffle(files)
        for path in files:
            if picked_here >= args.per_repo or len(samples) >= args.n:
                break
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                continue
            stats["files_read"] += 1
            try:
                mod = ast.parse(text)
            except (SyntaxError, ValueError, RecursionError):
                stats["discard_parse"] += 1
                continue
            lines = text.splitlines()
            funcs = [n for n in ast.walk(mod)
                     if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            rng.shuffle(funcs)
            for fn in funcs:
                if picked_here >= args.per_repo or len(samples) >= args.n:
                    break
                stats["funcs_seen"] += 1
                start = fn.lineno - 1
                end = getattr(fn, "end_lineno", None)
                if end is None:
                    continue
                nlines = end - start
                if nlines < MIN_LINES or nlines > MAX_LINES:
                    stats["discard_len"] += 1
                    continue
                src = "\n".join(lines[start:end])
                pair = make_pair(src)
                if pair is None:
                    stats["discard_rename"] += 1
                    continue
                h = hash(pair["orig"])
                if h in seen_bodies:
                    stats["discard_dup"] += 1
                    continue
                seen_bodies.add(h)
                samples.append({
                    "repo": repo.name,
                    "path": str(path.relative_to(repo)),
                    "name": fn.name,
                    "lines": nlines,
                    **pair,
                })
                picked_here += 1

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sample.json").write_text(json.dumps(
        {"stats": stats, "samples": samples,
         "n_repos_used": len({s["repo"] for s in samples})}, indent=1))
    print(f"sampled {len(samples)} functions from "
          f"{len({s['repo'] for s in samples})} repos")
    print(json.dumps(stats, indent=1))


# --------------------------------------------------------------------------
# embedding
# --------------------------------------------------------------------------

def embed_batch(client: httpx.Client, model: str, texts: list[str]) -> list[list[float]]:
    r = client.post(f"{OLLAMA}/api/embed",
                    json={"model": model, "input": texts, "keep_alive": "10m"},
                    timeout=600.0)
    r.raise_for_status()
    return r.json()["embeddings"]


def unit(a: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(a, axis=-1, keepdims=True)
    n[n == 0] = 1.0
    return a / n


def cmd_embed(args):
    data = json.loads((OUT / "sample.json").read_text())
    samples = data["samples"]
    if args.limit:
        samples = samples[: args.limit]

    prefix = args.prefix or ""
    key_o = "orig_doc" if args.with_docstrings else "orig"
    key_t = "twin_strict" if args.strict else "twin"
    if args.with_docstrings:
        key_t += "_doc"
    texts = [prefix + s[key_o] for s in samples] + [prefix + s[key_t] for s in samples]

    vecs: list[list[float]] = []
    client = httpx.Client()
    t0 = time.time()
    for i in range(0, len(texts), args.batch):
        chunk = texts[i: i + args.batch]
        vecs.extend(embed_batch(client, args.model, chunk))
        if (i // args.batch) % 10 == 0:
            done = min(i + args.batch, len(texts))
            el = time.time() - t0
            print(f"  {done}/{len(texts)}  {el:.0f}s  ({done/max(el,1e-9):.1f}/s)",
                  flush=True)
    elapsed = time.time() - t0

    arr = np.array(vecs, dtype=np.float32)
    tag = args.tag or args.model.replace(":", "_").replace("/", "_")
    tag += "__strict" if args.strict else "__plain"
    if args.with_docstrings:
        tag += "+docstrings"
    np.save(OUT / f"vec__{tag}.npy", arr)
    (OUT / f"meta__{tag}.json").write_text(json.dumps({
        "model": args.model, "dim": int(arr.shape[1]), "n_texts": int(arr.shape[0]),
        "n_pairs": len(samples), "seconds": elapsed,
        "sec_per_1000": elapsed / len(texts) * 1000,
        "prefix": prefix, "with_docstrings": bool(args.with_docstrings),
        "rename": "strict" if args.strict else "plain",
        "mean_raw_norm": float(np.linalg.norm(arr, axis=1).mean()),
    }, indent=1))
    print(f"{tag}: dim={arr.shape[1]} n={arr.shape[0]} {elapsed:.1f}s "
          f"({elapsed/len(texts)*1000:.1f}s/1000) "
          f"raw_norm={np.linalg.norm(arr, axis=1).mean():.4f}")


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------

def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """Rank-based ROC-AUC (ties counted as half)."""
    allv = np.concatenate([pos, neg])
    order = allv.argsort()
    ranks = np.empty(len(allv), dtype=np.float64)
    ranks[order] = np.arange(1, len(allv) + 1)
    # average ranks for ties
    srt = allv[order]
    i = 0
    while i < len(srt):
        j = i
        while j + 1 < len(srt) and srt[j + 1] == srt[i]:
            j += 1
        if j > i:
            ranks[order[i: j + 1]] = (i + 1 + j + 1) / 2
        i = j + 1
    n1, n0 = len(pos), len(neg)
    return float((ranks[:n1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def score(arr: np.ndarray, n_pairs: int, dims: int | None, seed: int = 7):
    if dims:
        arr = arr[:, :dims]
    v = unit(arr.astype(np.float64))
    o, t = v[:n_pairs], v[n_pairs:]
    clone = (o * t).sum(1)

    rng = np.random.default_rng(seed)
    j = rng.integers(0, n_pairs, n_pairs)
    same = j == np.arange(n_pairs)
    j[same] = (j[same] + 1) % n_pairs
    rand = (o * o[j]).sum(1)

    p95 = float(np.percentile(rand, 95))
    # Raw separation is not comparable across models: each model has its own
    # cosine scale (nomic's random pairs sit at ~0.60, Qwen's at ~0.28).  Cohen's
    # d divides the gap by the pooled spread, so it -- and AUC -- are the only
    # cross-model numbers worth reading.
    pooled = float(np.sqrt((clone.var() + rand.var()) / 2)) or 1e-9
    return {
        "cohens_d": float((clone.mean() - rand.mean()) / pooled),
        "clone_mean": float(clone.mean()), "clone_std": float(clone.std()),
        "random_mean": float(rand.mean()), "random_std": float(rand.std()),
        "separation": float(clone.mean() - rand.mean()),
        "auc": auc(clone, rand),
        "clone_above_random_p95": float((clone > p95).mean()),
        "random_p95": p95,
    }


def cmd_report(args):
    data = json.loads((OUT / "sample.json").read_text())
    rows = []
    for meta_path in sorted(OUT.glob("meta__*.json")):
        tag = meta_path.name[len("meta__"): -len(".json")]
        meta = json.loads(meta_path.read_text())
        arr = np.load(OUT / f"vec__{tag}.npy")
        n = meta["n_pairs"]
        variants = [(meta["dim"], "native")]
        if meta["dim"] > 768:
            variants.append((768, "MRL-768"))
        for d, label in variants:
            s = score(arr, n, None if label == "native" else d)
            rows.append({"tag": tag, "model": meta["model"], "variant": label,
                         "dim": d, "sec_per_1000": meta["sec_per_1000"],
                         "n_pairs": n, "raw_norm": meta["mean_raw_norm"],
                         "prefix": meta["prefix"],
                         "with_docstrings": meta["with_docstrings"],
                         "rename": meta.get("rename", "plain"), **s})
    (OUT / "results.json").write_text(json.dumps(
        {"stats": data["stats"], "rows": rows}, indent=1))

    hdr = (f"{'model':<34}{'variant':<10}{'dim':>6}{'clone':>8}{'rand':>8}"
           f"{'sep':>9}{'d':>7}{'AUC':>7}{'>p95':>7}{'s/1k':>8}")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda x: (x["rename"], -x["auc"])):
        print(f"{r['tag']:<34}{r['variant']:<10}{r['dim']:>6}"
              f"{r['clone_mean']:>8.3f}{r['random_mean']:>8.3f}"
              f"{r['separation']:>+9.3f}{r['cohens_d']:>7.2f}{r['auc']:>7.3f}"
              f"{r['clone_above_random_p95']:>7.2f}{r['sec_per_1000']:>8.1f}")


# --------------------------------------------------------------------------
# non-embedding reference signal: AST k-gram fingerprints
# --------------------------------------------------------------------------

def ast_kgrams(src: str, k: int = 5) -> set[int] | None:
    """Hashed k-shingles over the pre-order AST node-type sequence.

    Identifier-free by construction: only node types and operator kinds enter
    the sequence, so this is pure structure.  Included as the reference ceiling
    the embeddings are being judged against, computed on the same sample rather
    than quoted from a previous run.
    """
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError, RecursionError):
        return None
    seq = [type(n).__name__ for n in ast.walk(tree)]
    if len(seq) < k:
        return None
    return {hash(tuple(seq[i: i + k])) for i in range(len(seq) - k + 1)}


def cmd_fingerprint(args):
    data = json.loads((OUT / "sample.json").read_text())
    samples = data["samples"]
    key_t = "twin_strict" if args.strict else "twin"

    grams_o, grams_t, discarded = [], [], 0
    for s_ in samples:
        go, gt = ast_kgrams(s_["orig"]), ast_kgrams(s_[key_t])
        if go is None or gt is None:
            discarded += 1
            continue
        grams_o.append(go)
        grams_t.append(gt)

    jac = lambda a, b: len(a & b) / max(len(a | b), 1)
    clone = np.array([jac(a, b) for a, b in zip(grams_o, grams_t)])
    rng = np.random.default_rng(7)
    n = len(grams_o)
    j = rng.integers(0, n, n)
    same = j == np.arange(n)
    j[same] = (j[same] + 1) % n
    rand = np.array([jac(grams_o[i], grams_o[j[i]]) for i in range(n)])

    pooled = float(np.sqrt((clone.var() + rand.var()) / 2)) or 1e-9
    res = {
        "signal": "ast_kgram_jaccard_k5", "n_pairs": n, "discarded": discarded,
        "clone_mean": float(clone.mean()), "clone_std": float(clone.std()),
        "random_mean": float(rand.mean()), "random_std": float(rand.std()),
        "random_max": float(rand.max()),
        "separation": float(clone.mean() - rand.mean()),
        "cohens_d": float((clone.mean() - rand.mean()) / pooled),
        "auc": auc(clone, rand),
        "clone_above_random_p95": float((clone > np.percentile(rand, 95)).mean()),
    }
    (OUT / "fingerprint.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


# --------------------------------------------------------------------------
# secondary benchmark: natural-language symbol summaries
# --------------------------------------------------------------------------

def cmd_sample_summaries(args):
    """Build same-file-sibling vs cross-repo pairs from symbol summaries.

    This is the natural-language counterpart of the code benchmark: can the
    model tell that two summaries describe symbols from the same source file?
    That is the property needed to score a summary against its file/module/repo
    summary and flag the generic ones.  Written in the same shape as the code
    sample so it scores through the identical embed/report path.
    """
    from dotenv import load_dotenv
    from couchbase.auth import PasswordAuthenticator
    from couchbase.cluster import Cluster
    from couchbase.options import ClusterOptions

    load_dotenv(args.env)
    cluster = Cluster(
        os.environ.get("COUCHBASE_URL", "couchbase://localhost"),
        ClusterOptions(PasswordAuthenticator(
            "Administrator", os.environ["COUCHBASE_PASSWORD"])))

    rows = list(cluster.query(
        "SELECT d.repo_id, d.file_path, d.symbol_name, d.content "
        "FROM code_kosha d WHERE d.type='symbol_index' "
        "AND d.content IS NOT MISSING LIMIT $lim",
        lim=args.pool))

    by_file: dict[tuple, list] = {}
    for r in rows:
        if not r.get("content") or len(r["content"]) < 80:
            continue
        by_file.setdefault((r["repo_id"], r["file_path"]), []).append(r)

    rng = random.Random(args.seed)
    samples = []
    files = [k for k, v in by_file.items() if len(v) >= 2]
    rng.shuffle(files)
    for key in files:
        if len(samples) >= args.n:
            break
        group = by_file[key][:]
        rng.shuffle(group)
        a, b = group[0], group[1]
        if a["content"] == b["content"]:
            continue
        samples.append({
            "repo": a["repo_id"], "path": a["file_path"],
            "name": f"{a['symbol_name']}|{b['symbol_name']}", "lines": 0,
            "orig": a["content"], "twin": b["content"],
            "twin_strict": b["content"],
            "orig_doc": a["content"], "twin_doc": b["content"],
            "twin_strict_doc": b["content"],
        })

    OUT.mkdir(parents=True, exist_ok=True)
    stats = {"rows_fetched": len(rows), "files_with_2plus": len(files),
             "pairs": len(samples)}
    (OUT / "sample.json").write_text(json.dumps(
        {"stats": stats, "samples": samples,
         "n_repos_used": len({s["repo"] for s in samples})}, indent=1))
    print(json.dumps(stats, indent=1))
    print(f"repos: {len({s['repo'] for s in samples})}")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("sample")
    p.add_argument("--n", type=int, default=1200)
    p.add_argument("--per-repo", type=int, default=8)
    p.add_argument("--seed", type=int, default=1729)
    p.set_defaults(fn=cmd_sample)

    p = sub.add_parser("embed")
    p.add_argument("--model", required=True)
    p.add_argument("--prefix", default="")
    p.add_argument("--batch", type=int, default=8)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--tag", default="")
    p.add_argument("--with-docstrings", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="use the strict twin (attributes and strings renamed too)")
    p.set_defaults(fn=cmd_embed)

    p = sub.add_parser("report")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("fingerprint")
    p.add_argument("--strict", action="store_true")
    p.set_defaults(fn=cmd_fingerprint)

    p = sub.add_parser("sample-summaries")
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--pool", type=int, default=60000)
    p.add_argument("--seed", type=int, default=1729)
    p.add_argument("--env", default="../../.env")
    p.set_defaults(fn=cmd_sample_summaries)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
