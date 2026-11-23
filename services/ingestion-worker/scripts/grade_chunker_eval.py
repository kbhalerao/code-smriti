#!/usr/bin/env python3
"""Grade an eval_chunker_models.py run on quality, not just yield.

Two layers:

1. `mechanical` -- deterministic checks per chunk: is the cited line range
   inside the file, and is the chunk's `content` actually found at those lines
   (identifier overlap within the range +/- SLACK lines)? Plus within-model
   duplicates (same file, >=80% overlapping line span). No judgement involved.

2. `packets` / `score` -- blinded judging. `packets` writes one file per source
   file: the numbered source and every model's chunk set under shuffled labels
   (A/B/C...), with the label->model key kept separately. A judge (a Claude
   subagent, never one of the candidates) writes `<packet>.grade.json` next to
   each packet; `score` unblinds and aggregates.

Usage:
    uv run python scripts/grade_chunker_eval.py mechanical RESULTS.json
    uv run python scripts/grade_chunker_eval.py packets RESULTS.json OUTDIR
    uv run python scripts/grade_chunker_eval.py score RESULTS.json OUTDIR
"""
from __future__ import annotations

import json
import os
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPOS = Path(os.environ.get("REPOS_PATH", "/Users/kaustubh/code/codesmriti-repos"))
SLACK = 3
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


def load(results_path: str) -> dict:
    return json.load(open(results_path))


def grounding(chunk: dict, lines: list[str]) -> tuple[bool, float]:
    s, e = chunk.get("start_line") or 0, chunk.get("end_line") or 0
    valid = 1 <= s <= e <= len(lines)
    lo, hi = max(0, s - 1 - SLACK), min(len(lines), max(s, e) + SLACK)
    window = set(IDENT.findall("\n".join(lines[lo:hi])))
    toks = set(IDENT.findall(chunk.get("content") or ""))
    if not toks:
        return valid, 0.0
    return valid, len(toks & window) / len(toks)


def overlap(a: dict, b: dict) -> float:
    a0, a1, b0, b1 = a["start_line"], a["end_line"], b["start_line"], b["end_line"]
    inter = max(0, min(a1, b1) - max(a0, b0) + 1)
    span = min(a1 - a0 + 1, b1 - b0 + 1)
    return inter / span if span > 0 else 0.0


def mechanical(results_path: str) -> None:
    d = load(results_path)
    files = d["files"]
    first8 = set(files[:8])
    print(f"{'model':<16} {'set':<4} {'kept':>5} {'valid%':>7} {'grnd>=.5%':>9} {'grnd_med':>8} {'dups':>5} {'sec':>7} {'tok/s':>6}")
    for r in d["results"]:
        for label, subset in (("8", first8), ("20", set(files))):
            n = valid = grounded = dups = 0
            g = []
            for rel, chunks in r.get("chunks", {}).items():
                if rel not in subset:
                    continue
                lines = (REPOS / rel).read_text(errors="replace").splitlines()
                for i, c in enumerate(chunks):
                    n += 1
                    v, gr = grounding(c, lines)
                    valid += v
                    grounded += gr >= 0.5
                    g.append(gr)
                    if any(overlap(c, o) >= 0.8 for o in chunks[:i]
                           if o["start_line"] and c["start_line"]):
                        dups += 1
            sec = r["seconds"] if label == "20" else float("nan")
            tps = r.get("output_tokens", 0) / r["seconds"] if label == "20" and r["seconds"] else float("nan")
            print(f"{r['model']:<16} {label:<4} {n:>5} {100*valid/max(n,1):>6.0f}% {100*grounded/max(n,1):>8.0f}% "
                  f"{(statistics.median(g) if g else 0):>8.2f} {dups:>5} {sec:>7.0f} {tps:>6.1f}")


def packets(results_path: str, outdir: str) -> None:
    d = load(results_path)
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    models = [r["model"] for r in d["results"]]
    key = {}
    rng = random.Random(1123)
    for idx, rel in enumerate(d["files"]):
        labels = [chr(65 + i) for i in range(len(models))]
        order = models[:]
        rng.shuffle(order)
        key[rel] = dict(zip(labels, order))
        src = (REPOS / rel).read_text(errors="replace").splitlines()
        body = [f"# PACKET {idx:02d}: {rel}", "", "## SOURCE (line-numbered)", "```"]
        body += [f"{n:5}| {l}" for n, l in enumerate(src, 1)]
        body += ["```", ""]
        for lab, m in key[rel].items():
            r = next(x for x in d["results"] if x["model"] == m)
            chunks = r.get("chunks", {}).get(rel, [])
            body.append(f"## SET {lab} ({len(chunks)} chunks)")
            for j, c in enumerate(chunks, 1):
                body.append(json.dumps({
                    "id": f"{lab}{j}", "type": c.get("chunk_type"), "name": c.get("name"),
                    "lines": [c.get("start_line"), c.get("end_line")],
                    "content": c.get("content"), "purpose": c.get("purpose"),
                    "related_symbols": c.get("related_symbols"), "tags": c.get("tags"),
                }, ensure_ascii=False))
            body.append("")
        (out / f"packet_{idx:02d}.md").write_text("\n".join(body))
    (out / "KEY.json").write_text(json.dumps(key, indent=2))
    print(f"wrote {len(d['files'])} packets to {out}")


def score(results_path: str, outdir: str) -> None:
    d = load(results_path)
    out = Path(outdir)
    key = json.load(open(out / "KEY.json"))
    first8 = set(d["files"][:8])
    agg = {s: defaultdict(lambda: defaultdict(float)) for s in ("8", "20")}
    missing = []
    for idx, rel in enumerate(d["files"]):
        gp = out / f"packet_{idx:02d}.grade.json"
        if not gp.exists():
            missing.append(gp.name)
            continue
        g = json.load(open(gp))
        for lab, m in key[rel].items():
            per = [c for c in g.get("chunks", []) if str(c.get("id", "")).startswith(lab)]
            rank = (g.get("ranking") or []).index(lab) + 1 if lab in (g.get("ranking") or []) else None
            for s in ("8", "20"):
                if s == "8" and rel not in first8:
                    continue
                a = agg[s][m]
                a["chunks"] += len(per)
                a["correct"] += sum(1 for c in per if c.get("correct"))
                a["useful"] += sum(c.get("useful", 0) for c in per)
                a["useful2"] += sum(1 for c in per if c.get("useful", 0) >= 2)
                a["hallucinated"] += sum(1 for c in per if c.get("hallucinated"))
                a["redundant"] += sum(1 for c in per if c.get("redundant"))
                a["missed"] += len(g.get("missed", {}).get(lab, []))
                if rank:
                    a["rank_sum"] += rank
                    a["ranked"] += 1
                    a["wins"] += rank == 1
    if missing:
        print("missing grades:", missing)
    print(f"{'model':<16} {'set':<4} {'chunks':>6} {'correct%':>8} {'useful>=2':>9} {'useful_sum':>10} "
          f"{'halluc':>6} {'redund':>6} {'missed':>6} {'avg_rank':>8} {'wins':>5}")
    for s in ("8", "20"):
        for m, a in agg[s].items():
            n = a["chunks"] or 1
            print(f"{m:<16} {s:<4} {int(a['chunks']):>6} {100*a['correct']/n:>7.0f}% {int(a['useful2']):>9} "
                  f"{a['useful']:>10.0f} {int(a['hallucinated']):>6} {int(a['redundant']):>6} {int(a['missed']):>6} "
                  f"{a['rank_sum']/max(a['ranked'],1):>8.2f} {int(a['wins']):>5}")


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    {"mechanical": mechanical, "packets": packets, "score": score}[cmd](*rest)
