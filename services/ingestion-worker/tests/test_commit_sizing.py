"""
Tests for billing commit sizing.

The properties that matter to billing, in order:
- merge and root commits are never sized, but commits that arrived through a
  merge are (the --first-parent trap would drop most delivered work);
- a commit that already has a document is never re-sized or overwritten;
- a failed model call stores nothing, so it is retried;
- an all-excluded commit is stored as "excluded", not sent to the model.
"""

import subprocess
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4.incremental import commit_sizing as cs
from v4.incremental.commit_sizing import (
    CommitSizer, SizingStats, ai_attribution, build_state, candidate_commits, doc_key,
    is_excluded, split_diff,
)


# --- exclusions ------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "package-lock.json", "web/yarn.lock", "uv.lock", "go.sum", "Cargo.lock",
    "vendor/lib/x.go", "frontend/node_modules/a/index.js",
    "dist/app.js", "packages/ui/build/out.css",
    "app/migrations/0001_initial.py", "src/__snapshots__/a.test.js.snap",
    "logo.PNG", "icons/a.svg", "bundle.js.map", "data/fields.geojson",
    "static/app.min.js", "api/v1/service_pb2.py", "src/__generated__/types.ts",
])
def test_excluded(path):
    assert is_excluded(path)


@pytest.mark.parametrize("path", [
    "src/app.py", "README.md", "app/migrations/README.md",
    "app/migrations/__init__.py", "builder.py", "src/distance.ts",
    "docs/build-guide.md", "mapping.py",
])
def test_not_excluded(path):
    assert not is_excluded(path)


# --- diff handling ---------------------------------------------------------

DIFF = """diff --git a/src/app.py b/src/app.py
index 1..2 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1 @@
-a
+b
diff --git a/package-lock.json b/package-lock.json
index 1..2 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -1 +1 @@
-x
+y
diff --git a/my dir/b file.py b/my dir/b file.py
new file mode 100644
"""


def test_split_diff_paths():
    assert [p for p, _ in split_diff(DIFF)] == ["src/app.py", "package-lock.json", "my dir/b file.py"]


def test_build_state_drops_excluded_files():
    state, stats = build_state("fix: thing\n", DIFF)
    assert state.startswith("fix: thing\n\n")
    assert "src/app.py" in state and "package-lock" not in state
    assert stats == {"files": 3, "files_excluded": 1, "truncated": False}


def test_build_state_all_excluded():
    only_lock = DIFF.split("diff --git a/my dir")[0].split("diff --git a/package-lock")[1]
    state, stats = build_state("bump", "diff --git a/package-lock" + only_lock)
    assert state is None and stats["files_excluded"] == 1


def test_build_state_empty_diff_is_excluded():
    state, _ = build_state("empty", "")
    assert state is None


def test_build_state_truncates():
    big = "diff --git a/a.py b/a.py\n" + "+x\n" * 40_000
    state, stats = build_state("m", big)
    assert len(state) == cs.MAX_STATE_CHARS and stats["truncated"]


# --- git -------------------------------------------------------------------

def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(repo, name, msg):
    (repo / name).write_text(msg)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "r"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    return r


def test_candidates_skip_root_and_merges_but_keep_merged_work(repo):
    root = _commit(repo, "a.py", "root")
    base = _commit(repo, "b.py", "on main before")
    _git(repo, "checkout", "-q", "-b", "feature")
    f1 = _commit(repo, "c.py", "feature one")
    f2 = _commit(repo, "d.py", "feature two")
    _git(repo, "checkout", "-q", "main")
    m1 = _commit(repo, "e.py", "on main during")
    _git(repo, "merge", "-q", "--no-ff", "-m", "merge feature", "feature")
    merge = _git(repo, "rev-parse", "HEAD")

    got = candidate_commits(repo, merge, base=base, lookback_days=0)
    assert set(got) == {f1, f2, m1}
    assert merge not in got and base not in got

    # Lookback alone (a new repo): everything single-parent, still no root.
    got = candidate_commits(repo, merge, base=None, lookback_days=30)
    assert root not in got and merge not in got
    assert set(got) == {base, f1, f2, m1}


# --- sizer -----------------------------------------------------------------

class FakeExists:
    def __init__(self, v):
        self.exists = v


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def exists(self, key):
        return FakeExists(key in self.docs)

    def insert(self, key, doc):
        from couchbase.exceptions import DocumentExistsException
        if key in self.docs:
            raise DocumentExistsException()
        self.docs[key] = doc


def _sizer(handler, coll=None, **kw):
    s = CommitSizer(bucket=None, url="http://model/v1/systemone",
                    http=httpx.Client(transport=httpx.MockTransport(handler)), **kw)
    s._collection = coll or FakeCollection()
    return s


def _ok(size="M"):
    def handler(request):
        return httpx.Response(200, json={"answers": {"choice": {
            "choice": size, "probabilities": {"S": 0.1, "M": 0.7, "L": 0.2}}}})
    return handler


def test_sizes_and_stores_document(repo):
    _commit(repo, "a.py", "root")
    sha = _commit(repo, "b.py", "fix the thing")
    s = _sizer(_ok("M"))
    stats = s.size_repo("o/r", repo, sha, base=None)
    doc = s.collection.docs[doc_key("o/r", sha)]
    assert doc["type"] == "commit_size" and doc["size"] == "M"
    assert doc["p"] == {"S": 0.1, "M": 0.7, "L": 0.2}
    assert doc["model"] == "d1:3b" and doc["repo_id"] == "o/r" and doc["commit_hash"] == sha
    assert "sized_at" in doc and doc["author"] == "t@example.com"
    assert stats.sized == 1 and stats.by_class == {"M": 1}


def test_never_resizes_existing(repo):
    _commit(repo, "a.py", "root")
    sha = _commit(repo, "b.py", "x")
    coll = FakeCollection()
    coll.docs[doc_key("o/r", sha)] = {"size": "L", "from": "backfill"}
    calls = []
    s = _sizer(lambda r: calls.append(r) or httpx.Response(500), coll)
    stats = s.size_repo("o/r", repo, sha)
    assert calls == [] and stats.existing == 1
    assert coll.docs[doc_key("o/r", sha)] == {"size": "L", "from": "backfill"}


def test_failure_stores_nothing(repo):
    _commit(repo, "a.py", "root")
    sha = _commit(repo, "b.py", "x")
    s = _sizer(lambda r: httpx.Response(500))
    stats = s.size_repo("o/r", repo, sha)
    assert s.collection.docs == {} and stats.failed == 1


def test_bad_answer_stores_nothing(repo):
    _commit(repo, "a.py", "root")
    sha = _commit(repo, "b.py", "x")
    s = _sizer(lambda r: httpx.Response(200, json={"answers": {"choice": {
        "choice": "XL", "probabilities": {"XL": 1.0}}}}))
    s.size_repo("o/r", repo, sha)
    assert s.collection.docs == {}


def test_all_excluded_stored_without_model_call(repo):
    _commit(repo, "a.py", "root")
    sha = _commit(repo, "yarn.lock", "bump deps")
    calls = []
    s = _sizer(lambda r: calls.append(r) or httpx.Response(500))
    stats = s.size_repo("o/r", repo, sha)
    assert calls == []
    assert s.collection.docs[doc_key("o/r", sha)]["size"] == "excluded"
    assert stats.excluded == 1


def test_unreachable_model_stops_run(repo):
    _commit(repo, "a.py", "root")
    for i in range(3):
        sha = _commit(repo, f"f{i}.py", f"c{i}")
    calls = []

    def handler(r):
        calls.append(r)
        raise httpx.ConnectError("refused")

    s = _sizer(handler)
    s.size_repo("o/r", repo, sha)
    assert len(calls) == 1 and s.unavailable
    # The next repo in the same run is not attempted at all.
    s.size_repo("o/r2", repo, sha)
    assert len(calls) == 1


def test_consecutive_failures_trip_breaker(repo):
    _commit(repo, "a.py", "root")
    for i in range(6):
        sha = _commit(repo, f"f{i}.py", f"c{i}")
    calls = []
    s = _sizer(lambda r: calls.append(r) or httpx.Response(503))
    s.size_repo("o/r", repo, sha)
    assert len(calls) == cs.MAX_CONSECUTIVE_FAILURES and s.unavailable


def test_allowlist(repo):
    s = _sizer(_ok(), repo_globs=["me/*widget*", "acme/*", "me/gadget"])
    assert s.wants("me/widget") and s.wants("me/old_widget")
    assert s.wants("Acme/Anything")
    assert s.wants("me/gadget")
    assert not s.wants("fork-owner/widget")
    assert not s.wants("AcmeInc/thing")
    assert not s.wants("me/gadget-other")


def test_empty_allowlist_sizes_nothing(repo):
    s = _sizer(_ok(), repo_globs="".split(","))
    assert not s.wants("me/widget")
    sha = _commit(repo, "a.py", "root")
    assert s.size_repo("someone/else", repo, sha) == SizingStats()


# --- AI attribution --------------------------------------------------------

def test_ai_attribution_claude_trailers():
    msg = ("fix: thing\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\n"
           "Claude-Session: https://claude.ai/code/session_abc\n")
    assert ai_attribution(msg) == {"assisted": True, "tools": ["claude"],
                                   "sessions": ["https://claude.ai/code/session_abc"]}


def test_ai_attribution_none_and_body_mentions():
    assert ai_attribution("fix: thing") == {"assisted": False, "tools": []}
    # Talking about Claude in the body is not a trailer.
    assert not ai_attribution("docs: explain the claude co-authored-by policy")["assisted"]


def test_ai_attribution_other_tools():
    assert ai_attribution("x\n\nCo-authored-by: Copilot <c@github.com>")["tools"] == ["copilot"]


def test_document_records_ai(repo):
    _commit(repo, "a.py", "root")
    sha = _commit(repo, "b.py", "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>")
    s = _sizer(_ok("S"))
    s.size_repo("o/r", repo, sha)
    assert s.collection.docs[doc_key("o/r", sha)]["ai"] == {"assisted": True, "tools": ["claude"]}


def test_candidates_explicit_since_replaces_lookback(repo):
    _commit(repo, "a.py", "root")
    old = _commit(repo, "b.py", "old")
    tip = _commit(repo, "c.py", "new")
    # lookback 0 alone would return nothing; an explicit since returns history.
    assert candidate_commits(repo, tip, None, lookback_days=0) == []
    assert candidate_commits(repo, tip, None, lookback_days=0, since="1970-01-01") == [old, tip]
