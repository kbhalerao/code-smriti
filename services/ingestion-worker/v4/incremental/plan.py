"""
What a repository needs, decided once and executed later.

`process_repo` used to decide and act in one pass: fetch, compare commits, diff,
choose a mode, and do the work, all inside one call and one process. That is why
the queue could only ever live in memory — the decision was not a thing you could
hold, hand to something else, or resume.

A `RepoPlan` is that decision made durable. It is produced by the scan, which is
the only thing that touches the network, and consumed by the processor, which
touches only what the plan names. Two consequences the design depends on:

- **The processor does not fetch.** `GitOperations.fetch` writes — refspec config
  and `git remote set-head` — so a scan and a processor both fetching the same
  repo on the same tick collide on git's own locks. With the range pinned here,
  the scan is the only writer to `.git`.
- **The file list is exact, not advisory.** It is what will run, because it is
  what was decided against a specific commit range, so a dashboard showing it is
  showing the truth rather than a guess that origin/HEAD may already have moved
  past.

Everything here is JSON-serialisable on purpose: this is what gets written to the
queue document.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models import ChangeSet

# What the processor should do with this repo.
ACTION_NONE = "none"                # nothing to do; `status`/`reason` say why
ACTION_CLONE_INGEST = "clone_ingest"  # never ingested before — take the whole repo
ACTION_REBUILD = "rebuild"          # threshold exceeded, or the corpus is empty
ACTION_INCREMENTAL = "incremental"  # surgical update of the named files

# Actions that represent real work, i.e. things worth queueing.
WORK_ACTIONS = frozenset({ACTION_CLONE_INGEST, ACTION_REBUILD, ACTION_INCREMENTAL})


@dataclass
class RepoPlan:
    """One repository's decided work, pinned to a commit range."""

    repo_id: str
    action: str

    # Terminal outcome when action is ACTION_NONE — carried so the caller can
    # build the same UpdateResult the one-pass path used to return directly.
    status: str = ""
    reason: str = ""
    error: str = ""

    # The range this plan was decided against. `base_commit` is what the corpus
    # holds; `target_commit` is what it should hold. The processor works exactly
    # this range and never re-derives it.
    base_commit: Optional[str] = None
    target_commit: Optional[str] = None

    # Filtered to what the pipeline can actually ingest. Both sides of the
    # change-ratio comparison are drawn from these, which is what stopped
    # affiliate-sites reporting "1064 files changed (765.5%)" against a corpus
    # of 488 — a ratio over a population the corpus never contained.
    code_to_process: List[str] = field(default_factory=list)
    docs_to_process: List[str] = field(default_factory=list)
    code_deleted: List[str] = field(default_factory=list)
    docs_deleted: List[str] = field(default_factory=list)

    # Raw git counts, kept beside the filtered ones so a reader can see both.
    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)

    indexable_changed: int = 0
    corpus_files: int = 0
    rebuild_reason: Optional[str] = None

    @property
    def total_changed(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)

    @property
    def is_work(self) -> bool:
        return self.action in WORK_ACTIONS

    def as_changeset(self) -> ChangeSet:
        """The original diff, for the code paths that still want it."""
        return ChangeSet(added=self.added, modified=self.modified, deleted=self.deleted)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "action": self.action,
            "status": self.status,
            "reason": self.reason,
            "error": self.error,
            "base_commit": self.base_commit,
            "target_commit": self.target_commit,
            "code_to_process": self.code_to_process,
            "docs_to_process": self.docs_to_process,
            "code_deleted": self.code_deleted,
            "docs_deleted": self.docs_deleted,
            "added": self.added,
            "modified": self.modified,
            "deleted": self.deleted,
            "indexable_changed": self.indexable_changed,
            "corpus_files": self.corpus_files,
            "rebuild_reason": self.rebuild_reason,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RepoPlan":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})
