"""
Commit sizing for billing.

Each commit that reaches a repo's default branch is classed S / M / L by the
`d1:3b` decision model: the work a competent engineer with no AI help would need
for the change. A fit against billed hours, done in the StrategicPlanning
dashboard and not here, turns those classes into standard hours.

**What gets sized.** Every *single-parent* commit reachable from the commit the
corpus was just brought to. Two alternatives were measured on 312 clones over
2025-11-04..2026-11-04 and rejected:

- `--first-parent` would see 2,381 commits against 8,111, because it skips every
  commit that arrived through a merge commit. Those are most of the delivered work.
- All remote branches would add 2,571 commits, a large share of them rebased,
  squashed or cherry-picked copies of commits already on the default branch
  (132 of 300 distinct branch-only subjects in the busiest repo). Sizing them
  would bill the same work twice. Work on a branch is sized once it merges.

Merge commits and root commits are skipped: a merge's diff is not the work, and a
root commit is usually an import.

**Where it is stored.** `code_kosha.billing.commit_size`, a collection of its
own, never the `_default` collection the rest of the corpus lives in. Two
reasons, both structural:

- Six code paths run `DELETE FROM code_kosha WHERE repo_id = ...`, the full
  rebuild among them. In `_default` a rebuild would erase that repo's billing
  history.
- Every API, MCP and RAG query reads `FROM code_kosha`, which resolves to
  `_default._default`, and `code_vector_index` maps only that collection. A
  separate collection is out of their reach without anyone remembering to add a
  `type != 'commit_size'` filter. It also makes RBAC on billing data possible
  per collection.

**Idempotency.** A commit that already has a document is never re-sized; the
write is an `insert`, so a race loses quietly rather than overwriting. When the
model call fails nothing is stored, and the commit is picked up again by the
lookback window on the next run that touches the repo, or by the daily sweep
(`scripts/size_commits.py`).
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, List, Optional, Tuple

import httpx
from loguru import logger

SCOPE = "billing"
COLLECTION = "commit_size"
DOC_TYPE = "commit_size"
SIZE_EXCLUDED = "excluded"
CLASSES = ("S", "M", "L")

# A model that answers but keeps failing (timeouts, 5xx) is treated as down
# after this many commits in a row, rather than costing a timeout per commit.
MAX_CONSECUTIVE_FAILURES = 3

# ~16k-token context on d1:3b.
MAX_STATE_CHARS = 36_000

INSTRUCTIONS = (
    "Size this git commit for billing: the work a competent engineer with no AI "
    "help needs for this change, including the diagnosis the message implies. "
    "Generated, boilerplate or verbose code adds lines but little work, and a "
    "hard fix can be one line."
)
CRITERIA = {
    "S": "One concern in one place: a fix, a config or copy change, a small refactor, docs. Under half a day.",
    "M": "Crosses modules, adds a schema or API change, or a fix that needed real diagnosis. Half a day to two days.",
    "L": "A new feature, an integration, or a redesign. More than two days.",
}

# --- Exclusions ------------------------------------------------------------
# Files whose diff adds lines but not work. Matched against the basename and
# against each directory component.

_LOCKFILES = {
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
    "bun.lockb", "bun.lock", "poetry.lock", "uv.lock", "pipfile.lock",
    "cargo.lock", "gemfile.lock", "composer.lock", "go.sum", "flake.lock",
    "podfile.lock", "mix.lock", "pubspec.lock", "packages.lock.json",
    "gradle.lockfile",
}
_EXCLUDED_DIRS = {
    # vendored
    "vendor", "vendors", "node_modules", "third_party", "third-party",
    "bower_components", ".yarn",
    # build output
    "dist", "build", ".next", ".nuxt", ".svelte-kit", ".output", "coverage",
    # generated / snapshots
    "generated", "__generated__", "__snapshots__",
}
_EXCLUDED_GLOBS = (
    "*.lock",
    # generated
    "*.min.js", "*.min.css", "*_pb2.py", "*_pb2_grpc.py", "*.pb.go",
    "*.generated.*", "*.g.dart", "*.freezed.dart",
    # snapshots
    "*.snap",
    # maps and geodata
    "*.map", "*.geojson",
    # images
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.webp", "*.ico", "*.bmp",
    "*.tif", "*.tiff", "*.avif", "*.heic", "*.psd",
)


def is_excluded(path: str) -> bool:
    """Whether a changed file should be left out of the sizing diff."""
    p = PurePosixPath(path.lower())
    name = p.name
    if name in _LOCKFILES:
        return True
    if any(fnmatch.fnmatchcase(name, g) for g in _EXCLUDED_GLOBS):
        return True
    dirs = p.parts[:-1]
    if any(d in _EXCLUDED_DIRS for d in dirs):
        return True
    # Django-style numbered migrations: migrations/0001_initial.py
    if dirs and dirs[-1] == "migrations" and name.startswith("0"):
        return True
    return False


def split_diff(diff: str) -> List[Tuple[str, str]]:
    """Split a unified git diff into (path, section) pairs, one per file.

    The path is the post-image (`b/`) path, which is what a rename or an add
    names; for a deletion it is the pre-image path, since git writes both.
    """
    sections: List[Tuple[str, str]] = []
    current: List[str] = []
    path = ""
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current:
                sections.append((path, "".join(current)))
            current = [line]
            # diff --git a/<path> b/<path>; paths with spaces are why this
            # splits on " b/" from the right rather than on whitespace.
            rest = line[len("diff --git "):].rstrip("\n")
            path = rest.rsplit(" b/", 1)[-1] if " b/" in rest else rest
        elif current:
            current.append(line)
    if current:
        sections.append((path, "".join(current)))
    return sections


def build_state(message: str, diff: str) -> Tuple[Optional[str], Dict]:
    """Message plus filtered diff, truncated to MAX_STATE_CHARS.

    Returns (state, stats). `state` is None when every changed file is excluded,
    including a commit that changes no files at all.
    """
    sections = split_diff(diff)
    kept = [(p, s) for p, s in sections if not is_excluded(p)]
    stats = {"files": len(sections), "files_excluded": len(sections) - len(kept)}
    if not kept:
        return None, stats
    state = message.strip() + "\n\n" + "".join(s for _, s in kept)
    stats["truncated"] = len(state) > MAX_STATE_CHARS
    return state[:MAX_STATE_CHARS], stats


# --- AI attribution --------------------------------------------------------
# Billing prices human and AI effort differently, and the size alone cannot say
# which a commit was: it is the *counterfactual* no-AI effort, the value
# delivered, by design. What git does record is the tool's own trailer. That is
# evidence of presence, not of share — a commit with a Claude trailer may still
# be mostly the engineer's diagnosis, and Copilot or Cursor inline completion
# leaves no trailer at all — so it is stored as evidence for the dashboard's
# fit, not folded into the size. Measured 2026-11-04 over the billed repos:
# 75% of 5,685 single-parent commits carry a Claude trailer, 94-100% since
# 2026-08.

_AI_TRAILERS = (
    ("claude", re.compile(r"^co-authored-by:.*\b(claude|anthropic)\b", re.I | re.M)),
    ("claude", re.compile(r"^claude-session:", re.I | re.M)),
    ("claude", re.compile(r"generated with \[?claude", re.I)),
    ("codex", re.compile(r"^co-authored-by:.*\b(codex|openai|chatgpt)\b", re.I | re.M)),
    ("copilot", re.compile(r"^co-authored-by:.*copilot", re.I | re.M)),
    ("cursor", re.compile(r"^co-authored-by:.*cursor", re.I | re.M)),
    ("gemini", re.compile(r"^co-authored-by:.*gemini", re.I | re.M)),
    ("aider", re.compile(r"^co-authored-by:.*aider", re.I | re.M)),
)
_SESSION = re.compile(r"^claude-session:\s*(\S+)", re.I | re.M)


def ai_attribution(message: str) -> Dict:
    """What the commit message itself says about AI involvement."""
    tools = sorted({name for name, pat in _AI_TRAILERS if pat.search(message)})
    sessions = _SESSION.findall(message)
    out: Dict = {"assisted": bool(tools), "tools": tools}
    if sessions:
        out["sessions"] = sessions
    return out


# --- Git -------------------------------------------------------------------

def _git(repo_path: Path, args: List[str], timeout: int = 120) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo_path, capture_output=True, text=True,
        timeout=timeout, errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def candidate_commits(
    repo_path: Path,
    target: str,
    base: Optional[str] = None,
    lookback_days: int = 14,
    since: Optional[str] = None,
) -> List[str]:
    """Single-parent commits to consider sizing, oldest first.

    `base..target` covers what this run brought in. The lookback window over
    `target` is the retry path: a commit whose model call failed last time is
    still inside it. It is also all a newly cloned repo gets — its history is the
    backfill's job, not the tick's.
    """
    flags = ["rev-list", "--reverse", "--min-parents=1", "--max-parents=1"]
    shas: List[str] = []
    if base:
        shas += _git(repo_path, flags + [f"{base}..{target}"]).split()
    if since:
        # An explicit window (a deliberate backfill) replaces the lookback.
        shas += _git(repo_path, flags + [f"--since={since}", target]).split()
    elif lookback_days > 0:
        shas += _git(repo_path, flags + [f"--since={lookback_days}.days.ago", target]).split()
    return list(dict.fromkeys(shas))  # de-duplicate, keep order


def commit_payload(repo_path: Path, sha: str) -> Tuple[str, str, str, str]:
    """(message, diff, author_email, author_date) for one single-parent commit."""
    meta = _git(repo_path, ["log", "-1", "--format=%ae%x00%aI%x00%B", sha])
    author, date, message = meta.split("\x00", 2)
    diff = _git(repo_path, ["diff", "--no-color", "--no-ext-diff", "-M", f"{sha}^", sha])
    return message, diff, author, date


# --- Sizer -----------------------------------------------------------------

class SizingUnavailable(Exception):
    """The decision model could not be reached; stop trying for this run."""


class ContextTooLong(Exception):
    """The state tokenised past the model's context. MAX_STATE_CHARS assumes
    code-like density; HTML and markup run denser (36,000 chars of a static
    site's pages came to 16,541 tokens against a 16,384 context)."""

    def __init__(self, tokens: int, limit: int):
        super().__init__(f"{tokens} tokens > {limit}")
        self.tokens, self.limit = tokens, limit


_CONTEXT_ERROR = re.compile(r"\((\d+) tokens\) exceeds the available context size \((\d+) tokens\)")


@dataclass
class SizingStats:
    sized: int = 0
    excluded: int = 0
    existing: int = 0
    failed: int = 0
    by_class: Dict[str, int] = field(default_factory=dict)

    def __str__(self) -> str:
        classes = " ".join(f"{k}={v}" for k, v in sorted(self.by_class.items()))
        return (f"sized {self.sized} ({classes or 'none'}), excluded {self.excluded}, "
                f"already sized {self.existing}, failed {self.failed}")


def doc_key(repo_id: str, sha: str) -> str:
    return f"commit_size:{repo_id}@{sha}"


class CommitSizer:
    def __init__(self, bucket, url: str, model: str = "d1:3b",
                 timeout: float = 120.0, lookback_days: int = 14,
                 repo_globs: Iterable[str] = ("*",),
                 http: Optional[httpx.Client] = None):
        self.bucket = bucket
        self.url = url
        self.model = model
        self.timeout = timeout
        self.lookback_days = lookback_days
        self.repo_globs = tuple(g.strip().lower() for g in repo_globs if g.strip())
        self.http = http or httpx.Client(timeout=timeout)
        self._collection = None
        # Set on the first connection-level failure so a down model costs one
        # timeout per run rather than one per commit.
        self.unavailable = False

    def wants(self, repo_id: str) -> bool:
        """Whether this repo is in the billing allowlist (globs over owner/name)."""
        rid = repo_id.lower()
        return any(fnmatch.fnmatchcase(rid, g) for g in self.repo_globs)

    # -- storage --

    @property
    def collection(self):
        if self._collection is None:
            self._collection = self.bucket.scope(SCOPE).collection(COLLECTION)
        return self._collection

    def ensure_collection(self, cluster=None) -> None:
        """Create the billing scope and collection if missing. Idempotent."""
        from couchbase.exceptions import (
            CollectionAlreadyExistsException, ScopeAlreadyExistsException,
        )
        mgr = self.bucket.collections()
        try:
            mgr.create_scope(SCOPE)
        except ScopeAlreadyExistsException:
            pass
        try:
            mgr.create_collection(SCOPE, COLLECTION)
        except CollectionAlreadyExistsException:
            pass
        if cluster is not None:
            # For the dashboard's N1QL reads; KV access by key needs none.
            name = self.bucket.name
            list(cluster.query(
                f"CREATE PRIMARY INDEX IF NOT EXISTS ON `{name}`.`{SCOPE}`.`{COLLECTION}`"
            ))

    def exists(self, repo_id: str, sha: str) -> bool:
        return self.collection.exists(doc_key(repo_id, sha)).exists

    def store(self, repo_id: str, sha: str, doc: Dict) -> bool:
        from couchbase.exceptions import DocumentExistsException
        try:
            self.collection.insert(doc_key(repo_id, sha), doc)
            return True
        except DocumentExistsException:
            return False

    # -- model --

    def classify(self, state: str) -> Tuple[str, Dict[str, float]]:
        body = {
            "model": self.model,
            "state": state,
            "questions": {"choice": {
                "type": "choice", "instructions": INSTRUCTIONS, "criteria": CRITERIA,
            }},
        }
        try:
            resp = self.http.post(self.url, json=body, timeout=self.timeout)
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            raise SizingUnavailable(str(e)) from e
        if resp.status_code == 400:
            m = _CONTEXT_ERROR.search(resp.text)
            if m:
                raise ContextTooLong(int(m.group(1)), int(m.group(2)))
        resp.raise_for_status()
        answer = resp.json()["answers"]["choice"]
        size = answer["choice"]
        probs = {k: float(v) for k, v in answer["probabilities"].items()}
        if size not in CLASSES or set(probs) != set(CLASSES):
            raise ValueError(f"unexpected answer: {answer!r}")
        return size, probs

    def _classify_fitting(self, state: str, file_stats: Dict) -> Tuple[str, Dict[str, float]]:
        """Classify, shrinking the state in proportion to any context overshoot."""
        for _ in range(3):
            try:
                return self.classify(state)
            except ContextTooLong as e:
                state = state[:int(len(state) * e.limit / e.tokens * 0.9)]
                file_stats["truncated"] = True
        return self.classify(state)

    # -- orchestration --

    def size_commit(self, repo_id: str, repo_path: Path, sha: str, stats: SizingStats) -> None:
        if self.exists(repo_id, sha):
            stats.existing += 1
            return
        try:
            message, diff, author, date = commit_payload(repo_path, sha)
            state, file_stats = build_state(message, diff)
            if state is None:
                size, probs = SIZE_EXCLUDED, {}
            else:
                size, probs = self._classify_fitting(state, file_stats)
        except SizingUnavailable:
            raise
        except Exception as e:
            # Store nothing: the next run's lookback retries it.
            stats.failed += 1
            logger.warning(f"    Could not size {repo_id}@{sha[:8]}: {e}")
            return

        doc = {
            "type": DOC_TYPE,
            "repo_id": repo_id,
            "commit_hash": sha,
            "commit_date": date,
            "author": author,
            "size": size,
            "p": probs,
            "model": self.model,
            "sized_at": datetime.now(timezone.utc).isoformat(),
            "ai": ai_attribution(message),
            **file_stats,
        }
        if not self.store(repo_id, sha, doc):
            stats.existing += 1
            return
        if size == SIZE_EXCLUDED:
            stats.excluded += 1
        else:
            stats.sized += 1
            stats.by_class[size] = stats.by_class.get(size, 0) + 1

    def size_repo(self, repo_id: str, repo_path: Path, target: str,
                  base: Optional[str] = None, since: Optional[str] = None) -> SizingStats:
        """Size every unsized candidate commit for one repo. Never raises."""
        stats = SizingStats()
        if self.unavailable or not self.wants(repo_id):
            return stats
        try:
            shas = candidate_commits(repo_path, target, base, self.lookback_days, since)
        except Exception as e:
            logger.warning(f"    Commit sizing skipped for {repo_id}: {e}")
            return stats
        consecutive = 0
        for sha in shas:
            failed_before = stats.failed
            try:
                self.size_commit(repo_id, repo_path, sha, stats)
                consecutive = consecutive + 1 if stats.failed > failed_before else 0
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    raise SizingUnavailable(f"{consecutive} consecutive failures")
            except SizingUnavailable as e:
                self.unavailable = True
                logger.warning(f"    Commit sizing unavailable ({self.url}): {e}; "
                               f"skipping for the rest of this run")
                break
        if stats.sized or stats.excluded or stats.failed:
            logger.info(f"    Commit sizing: {stats}")
        return stats


def sizer_from_config(cb_client, config) -> Optional[CommitSizer]:
    """The configured sizer, or None when sizing is disabled or cannot start."""
    if not config.commit_sizing_enabled:
        return None
    try:
        sizer = CommitSizer(
            cb_client.bucket,
            url=config.commit_size_url,
            model=config.commit_size_model,
            timeout=config.commit_size_timeout_seconds,
            lookback_days=config.commit_size_lookback_days,
            repo_globs=config.commit_size_repos.split(","),
        )
        sizer.ensure_collection(cb_client.cluster)
        return sizer
    except Exception as e:
        logger.warning(f"Commit sizing disabled for this run: {e}")
        return None
