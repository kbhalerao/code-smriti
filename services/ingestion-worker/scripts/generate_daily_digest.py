#!/usr/bin/env python3
"""
Daily Ingestion Digest -> Chief of Staff

Runs as the final step of the daily incremental ingestion (see run_incremental.sh).
Reads every `ingestion_run` doc in the last --since-hours (default 24) and merges
them, pulls the `commit_index`
docs it produced, synthesizes a per-repo / per-author markdown summary via the
configured LLM, and POSTs the result as a `note` into the
Chief of Staff inbox (tags=['updates'], status=inbox, priority=low).

Originating idea: cos doc fe595161.

Usage:
    uv run python scripts/generate_daily_digest.py             # post to cos
    uv run python scripts/generate_daily_digest.py --dry-run   # print, don't post
    uv run python scripts/generate_daily_digest.py --run-id YYYYMMDD_HHMMSS_xxx  # specific run
"""

import argparse
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import WorkerConfig
from llm_enricher import LLMEnricher, LLM_CONFIG, LLMConfig, LLMUnavailableError
from storage.couchbase_client import CouchbaseClient

config = WorkerConfig()

# Content cap on the cos side is 10,000 chars (CreateDocRequest). Leave headroom.
COS_CONTENT_CAP = 9_800

# Token / context budget for the LLM call. ~150 commits across all repos is the
# soft cap before we start dropping low-churn repos to keep the prompt sane.
MAX_COMMITS_IN_PROMPT = 150

# Per-commit file list truncation — full file lists can be hundreds of paths for
# vendored / generated diffs.
MAX_FILES_PER_COMMIT = 5

# Statuses that mean "this repo actually had ingested changes in this run".
ACTIVE_STATUSES = {"updated", "full_reingest"}


def fetch_latest_run(cb: CouchbaseClient, run_id: str | None) -> dict | None:
    """Return the latest ingestion_run doc (or a specific one by run_id)."""
    bucket = config.couchbase_bucket
    if run_id:
        result = cb.cluster.query(
            f"SELECT META().id as _id, b.* FROM `{bucket}` b "
            f"WHERE type = 'ingestion_run' AND run_id = $rid LIMIT 1",
            rid=run_id,
        )
    else:
        result = cb.cluster.query(
            f"SELECT META().id as _id, b.* FROM `{bucket}` b "
            f"WHERE type = 'ingestion_run' ORDER BY timestamp DESC LIMIT 1"
        )
    rows = list(result)
    return rows[0] if rows else None


def fetch_runs_since(cb: CouchbaseClient, since_iso: str) -> list[dict]:
    """Every ingestion_run in the window, oldest first."""
    bucket = config.couchbase_bucket
    result = cb.cluster.query(
        f"SELECT META().id as _id, b.* FROM `{bucket}` b "
        f"WHERE type = 'ingestion_run' AND timestamp >= $since ORDER BY timestamp ASC",
        since=since_iso,
    )
    return list(result)


def merge_runs(runs: list[dict]) -> dict:
    """Collapse a window of runs into one synthetic run doc.

    The digest was written when a day held exactly one ingestion run, so "the
    most recent ingestion_run doc" and "what happened today" were the same
    sentence. Under a five-minute tick they are not: the newest run doc is the
    last drain, which is often a single repo and sometimes nothing. Reading it
    would turn a daily digest into a report on whichever five minutes happened
    to end last.

    A repo's status is taken from the most significant run it appeared in, not
    the latest — a repo updated at 09:00 and skipped at 14:55 was still updated
    today, and the digest is about what changed.
    """
    merged_repos: dict[str, dict] = {}
    processed = 0

    for run in runs:
        processed += (run.get("stats") or {}).get("processed", 0)
        for repo_id, info in (run.get("repos") or {}).items():
            info = info or {}
            prev = merged_repos.get(repo_id)
            if prev and prev.get("status") in ACTIVE_STATUSES and info.get("status") not in ACTIVE_STATUSES:
                continue
            merged_repos[repo_id] = info

    return {
        "run_id": f"{len(runs)} run(s)" if len(runs) != 1 else (runs[0].get("run_id") or "unknown"),
        "timestamp": runs[0].get("timestamp") if runs else "",
        "completed_at": runs[-1].get("completed_at") if runs else "",
        "repos": merged_repos,
        "stats": {"processed": processed},
    }


def fetch_commits_for_run(cb: CouchbaseClient, repos: list[str], since: str) -> list[dict]:
    """Pull all commit_index docs ingested during this run.

    Filters by `version.created_at` (set at ingestion time) rather than
    `commit_date` (the author's commit timestamp). A commit can be authored
    weeks ago but ingested today — we want everything ingested today.
    """
    if not repos:
        return []
    bucket = config.couchbase_bucket
    result = cb.cluster.query(
        f"""
        SELECT repo_id, commit_hash, commit_date, author,
               content as message, metadata, version.created_at as ingested_at
        FROM `{bucket}`
        WHERE type = 'commit_index'
          AND repo_id IN $repos
          AND version.created_at >= $since
        ORDER BY version.created_at DESC
        """,
        repos=repos,
        since=since,
    )
    return list(result)


def group_commits(commits: list[dict]) -> dict[str, dict[str, list[dict]]]:
    """Group commits as repo -> author -> [commit]."""
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for c in commits:
        grouped[c["repo_id"]][c.get("author") or "unknown"].append(c)
    return grouped


def churn(commit: dict) -> int:
    md = commit.get("metadata") or {}
    return int(md.get("lines_added") or 0) + int(md.get("lines_deleted") or 0)


def trim_to_budget(grouped: dict[str, dict[str, list[dict]]]) -> dict[str, dict[str, list[dict]]]:
    """If total commits exceed MAX_COMMITS_IN_PROMPT, keep the highest-churn repos."""
    total = sum(len(cs) for authors in grouped.values() for cs in authors.values())
    if total <= MAX_COMMITS_IN_PROMPT:
        return grouped

    repo_churn = {
        repo: sum(churn(c) for cs in authors_d.values() for c in cs)
        for repo, authors_d in grouped.items()
    }
    repos_by_churn = sorted(repo_churn.items(), key=lambda kv: kv[1], reverse=True)

    kept: dict[str, dict[str, list[dict]]] = {}
    running = 0
    for repo, _ in repos_by_churn:
        count = sum(len(cs) for cs in grouped[repo].values())
        if running + count > MAX_COMMITS_IN_PROMPT and kept:
            # Already have some repos; stop adding more.
            break
        kept[repo] = grouped[repo]
        running += count
    return kept


def build_prompt(
    run_doc: dict,
    grouped: dict[str, dict[str, list[dict]]],
    digest_date: str,
) -> str:
    """Render the structured payload + instructions for the LLM."""
    sections: list[str] = []
    for repo, authors in sorted(grouped.items(), key=lambda kv: -sum(len(cs) for cs in kv[1].values())):
        author_blocks: list[str] = []
        # Sort authors within repo by total churn desc.
        for author, commits in sorted(
            authors.items(),
            key=lambda kv: -sum(churn(c) for c in kv[1]),
        ):
            commit_lines: list[str] = []
            for c in commits:
                files = (c.get("metadata") or {}).get("files_changed", []) or []
                file_blurb = ""
                if files:
                    shown = files[: MAX_FILES_PER_COMMIT]
                    extra = len(files) - len(shown)
                    file_blurb = " | files: " + ", ".join(shown)
                    if extra > 0:
                        file_blurb += f" (+{extra} more)"
                msg = (c.get("message") or "").strip().replace("\n", " ")[:200]
                commit_lines.append(
                    f"  - {c['commit_hash'][:8]} +{(c.get('metadata') or {}).get('lines_added', 0)}/"
                    f"-{(c.get('metadata') or {}).get('lines_deleted', 0)}: {msg}{file_blurb}"
                )
            author_blocks.append(f"- {author} ({len(commits)} commit(s)):\n" + "\n".join(commit_lines))
        sections.append(f"### {repo}\n" + "\n".join(author_blocks))

    payload = "\n\n".join(sections)
    repos_n = len(grouped)
    commits_n = sum(len(cs) for authors in grouped.values() for cs in authors.values())
    authors_n = len({a for authors in grouped.values() for a in authors})

    return f"""You are writing a one-day developer activity digest for the user's "Chief of Staff" inbox. Your job is to synthesize what shipped across multiple codebases today, attributed by author.

Run window: {run_doc.get('timestamp')} to {run_doc.get('completed_at')}
Digest date: {digest_date}
Scope: {repos_n} repos, {commits_n} commits, {authors_n} authors.

Raw data (per-repo, per-author commits with churn and file lists):

{payload}

Write a markdown digest with this exact shape:

# Daily digest — {digest_date}

**TL;DR.** 2-3 sentences. Synthesize the headline of the day across all repos. Name the most consequential work and who did it. Be specific (mention what shipped, not just "many commits").

## Repos

For each repo (highest-churn first), write:

### <repo_id> (<n> commits, <m> authors)
One-sentence "what shipped" — the theme of the day's work in this repo.

- **<author email>** — one line summarizing this author's commits in this repo. Cite churn (+X/-Y) once at the end of the line. Don't list every commit verbatim; synthesize.

Hard rules:
- Stay under 8000 characters total.
- Don't editorialize ("great work!", "impressive"). Be a neutral observer.
- Don't repeat the raw data — synthesize.
- Use the author's email exactly as given.
- If a repo has only bot commits (e.g. auto-format, dep bumps), say so in one line and move on.
- Output only the markdown. No preamble, no closing remarks."""


def fallback_markdown(
    grouped: dict[str, dict[str, list[dict]]],
    digest_date: str,
    reason: str,
) -> str:
    """Render a structured digest without LLM synthesis (used when gemma is down)."""
    lines = [
        f"# Daily digest — {digest_date}",
        "",
        f"_LLM synthesis unavailable ({reason}); showing raw rollup._",
        "",
        "## Repos",
        "",
    ]
    for repo, authors in sorted(grouped.items(), key=lambda kv: -sum(len(cs) for cs in kv[1].values())):
        commits_n = sum(len(cs) for cs in authors.values())
        lines.append(f"### {repo} ({commits_n} commits, {len(authors)} authors)")
        for author, commits in sorted(
            authors.items(),
            key=lambda kv: -sum(churn(c) for c in kv[1]),
        ):
            added = sum(int((c.get("metadata") or {}).get("lines_added") or 0) for c in commits)
            deleted = sum(int((c.get("metadata") or {}).get("lines_deleted") or 0) for c in commits)
            lines.append(f"- **{author}** — {len(commits)} commit(s), +{added}/-{deleted}")
        lines.append("")
    return "\n".join(lines)


async def synthesize(grouped: dict[str, dict[str, list[dict]]], run_doc: dict, digest_date: str) -> tuple[str, str]:
    """Call the configured LLM. Returns (markdown, mode) where mode is 'llm' or 'fallback'."""
    # Thinking is off, matching LLM_REASONING_EFFORT=none and every other call
    # site (generate_bdr.py, backfill_module_summaries.py).
    #
    # This used to pass reasoning_effort="medium". On the current model
    # (`general` -> gemma4 26b-a4b) thinking is binary, so the whole scale
    # collapses: "low"/"medium"/"high"/omitted are all just thinking ON, and
    # only "none" turns it off. Thinking on, this prompt consumed 5.2k-8k+
    # output tokens across runs — straddling the 8000 cap, so roughly one run in
    # five spent the entire budget reasoning and emitted an empty message.
    #
    # Measured on the 2026-08-17 run, same prompt: thinking on took 80s / 7364
    # tokens; thinking off took 4s / 428 tokens and produced a slightly more
    # specific digest. build_prompt already hands the model a complete
    # per-repo/per-author rollup, so there is nothing left for it to reason out.
    cfg = LLMConfig(
        provider=LLM_CONFIG.provider,
        model=LLM_CONFIG.model,
        base_url=LLM_CONFIG.base_url,
        temperature=0.3,
        max_tokens=8000,
        timeout_seconds=600.0,
        reasoning_effort="none",
    )
    enricher = LLMEnricher(cfg)
    try:
        prompt = build_prompt(run_doc, grouped, digest_date)
        logger.info(f"Calling {cfg.model} with prompt length {len(prompt)} chars")
        out = (await enricher.generate(prompt)).strip()
        if not out:
            # generate() raises on an empty message, so reaching here means an
            # alternate extraction path produced nothing. Never return "" —
            # cos rejects empty content with a 422.
            logger.warning("LLM returned empty markdown; using fallback rollup")
            return fallback_markdown(grouped, digest_date, "empty LLM response"), "fallback"
        return out, "llm"
    except LLMUnavailableError as e:
        logger.warning(f"LLM unavailable: {e}; using fallback rollup")
        return fallback_markdown(grouped, digest_date, str(e)), "fallback"
    except Exception as e:
        logger.warning(f"LLM call failed: {e}; using fallback rollup")
        return fallback_markdown(grouped, digest_date, str(e)), "fallback"
    finally:
        await enricher.close()


def post_to_cos(
    markdown: str,
    title: str,
    run_id: str,
    digest_date: str,
    mode: str,
    counts: dict,
) -> bool:
    """POST the digest as a `note` into the cos inbox. Returns True on success."""
    if not config.cos_api_url or not config.cos_token:
        logger.error(
            "cos_api_url or cos_token not set; skipping POST. "
            "Set COS_API_URL and COS_TOKEN in .env."
        )
        return False

    # cos requires content of at least 1 character. Catch an empty body here
    # rather than spending a round trip to be told so by a 422.
    if not markdown.strip():
        logger.error("Refusing to POST an empty digest; nothing to send.")
        return False

    # Hard cap on content to fit the cos schema.
    if len(markdown) > COS_CONTENT_CAP:
        markdown = markdown[: COS_CONTENT_CAP - 40].rstrip() + "\n\n_(truncated to fit cos cap)_"

    payload = {
        "doc_type": "note",
        "content": markdown,
        "title": title[:200],
        "tags": ["updates"],
        "status": "inbox",
        "priority": "low",
        "project_id": config.cos_digest_project_id,
        "source": {"client": "code-smriti-ingestion", "project": "code-smriti"},
        "metadata": {
            "run_id": run_id,
            "digest_date": digest_date,
            "synthesis_mode": mode,
            **counts,
        },
    }
    url = f"{config.cos_api_url}/api/cos/docs"
    headers = {"Authorization": f"Bearer {config.cos_token}"}
    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=30.0)
    except httpx.RequestError as e:
        logger.error(f"cos POST transport error: {e}")
        return False
    if resp.status_code >= 400:
        logger.error(f"cos POST failed: {resp.status_code} {resp.text[:300]}")
        return False
    body = resp.json()
    doc_id = body.get("doc_id") or body.get("id") or body.get("document_id") or "?"
    logger.info(f"Posted digest to cos: doc_id={doc_id}, status={resp.status_code}")
    return True


async def amain(args: argparse.Namespace) -> int:
    digest_date = datetime.now().strftime("%Y-%m-%d")

    cb = CouchbaseClient()
    if args.run_id:
        run_doc = fetch_latest_run(cb, args.run_id)
        if not run_doc:
            logger.error("No ingestion_run doc found; nothing to digest.")
            return 1
    else:
        since = (datetime.now() - timedelta(hours=args.since_hours)).isoformat()
        runs = fetch_runs_since(cb, since)
        if not runs:
            logger.error(
                f"No ingestion_run doc in the last {args.since_hours}h; nothing to digest."
            )
            return 1
        logger.info(f"Digesting {len(runs)} run(s) since {since}")
        run_doc = merge_runs(runs)

    run_id = run_doc.get("run_id") or "unknown"
    started_at = run_doc.get("timestamp") or ""
    repos_dict = run_doc.get("repos") or {}
    active_repos = [
        repo_id
        for repo_id, info in repos_dict.items()
        if (info or {}).get("status") in ACTIVE_STATUSES
    ]

    logger.info(
        f"Run {run_id}: {len(active_repos)} active repo(s) of {len(repos_dict)} total; "
        f"started_at={started_at}"
    )

    if not active_repos:
        # Post the "nothing today" marker so the user knows the job ran.
        title = f"Daily digest — {digest_date} — no repo changes"
        body = (
            f"# Daily digest — {digest_date}\n\n"
            f"_No repo changes in today's ingestion run "
            f"(run `{run_id}`, processed {run_doc.get('stats', {}).get('processed', 0)} repos)._"
        )
        if args.dry_run:
            print(body)
            return 0
        ok = post_to_cos(
            body, title, run_id, digest_date, "empty",
            {"repos_active": 0, "commits": 0, "authors": 0},
        )
        return 0 if ok else 1

    commits = fetch_commits_for_run(cb, active_repos, started_at)
    if not commits:
        logger.warning(
            f"Run {run_id} reported {len(active_repos)} active repos but no commit_index "
            f"docs since {started_at}. Posting empty marker."
        )
        title = f"Daily digest — {digest_date} — no commits indexed"
        body = (
            f"# Daily digest — {digest_date}\n\n"
            f"_Run `{run_id}` touched {len(active_repos)} repo(s) but no commit_index docs "
            f"were found since {started_at}. Possible silent ingestion path._"
        )
        if args.dry_run:
            print(body)
            return 0
        ok = post_to_cos(
            body, title, run_id, digest_date, "no_commits",
            {"repos_active": len(active_repos), "commits": 0, "authors": 0},
        )
        return 0 if ok else 1

    grouped = group_commits(commits)
    grouped = trim_to_budget(grouped)

    counts = {
        "repos_active": len(active_repos),
        "repos_in_digest": len(grouped),
        "commits": sum(len(cs) for authors in grouped.values() for cs in authors.values()),
        "authors": len({a for authors in grouped.values() for a in authors}),
    }

    markdown, mode = await synthesize(grouped, run_doc, digest_date)
    title = (
        f"Daily digest — {digest_date} — "
        f"{counts['repos_in_digest']} repo(s), {counts['commits']} commit(s)"
    )

    if args.dry_run:
        print(f"# Title: {title}")
        print(f"# Mode: {mode}")
        print(f"# Counts: {counts}")
        print("---")
        print(markdown)
        return 0

    # Non-zero on a failed post. run_incremental.sh already fail-softs the run's
    # own exit code around this step (`set +e`) and turns a non-zero digest into
    # a high-priority cos alert — swallowing the failure here is what kept nine
    # missing digests invisible.
    ok = post_to_cos(markdown, title, run_id, digest_date, mode, counts)
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout, do not POST")
    parser.add_argument(
        "--since-hours",
        type=int,
        default=24,
        help="Digest every ingestion run in this window (default: 24). Ignored "
             "when --run-id names one run. Under a 5-minute tick a 'day' is many "
             "runs, so the window is what makes a daily digest daily."
    )
    parser.add_argument("--run-id", type=str, default=None, help="Specific run_id to digest")
    args = parser.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
