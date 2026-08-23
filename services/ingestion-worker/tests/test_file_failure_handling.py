"""
A file that fails must not vanish, and must not fail quietly.

Before this, `pipeline.process_files` caught the exception and returned
`(None, [], [])` — no `file_index` document at all — while the commits index
advanced and declared the repo current. There was also no per-file wall clock:
`llm_timeout_seconds` is per *request*, and a file issues one request per
significant symbol plus chunker passes, so a large file had no ceiling short of
the run watchdog.

Two rules are pinned here:

1. Degraded, never dropped. A file that blows its budget is reprocessed with the
   LLM off and lands a complete BASIC document marked `timeout_fallback`.
2. Retried at the end of the pass, not in place. A file usually fails because its
   own batch saturated the LLM slots; retrying while that batch is still in
   flight recreates the condition that caused it.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4 import pipeline as pipeline_module
from v4.pipeline import V4Pipeline
from v4.quality import QualityTracker
from v4.schemas import (
    FailureKind,
    FileFailure,
    FileIndex,
    ProcessedFile,
    QualityInfo,
    SymbolIndex,
)

# v4/pipeline.py builds its OWN WorkerConfig at import time, so patching a
# separate instance changes nothing the pipeline reads. Patch the one it uses.
config = pipeline_module.config


def _file_doc(rel_path: str) -> FileIndex:
    return FileIndex(
        document_id=f"file:{rel_path}",
        repo_id="owner/repo",
        file_path=rel_path,
        commit_hash="abc123",
        content="summary",
        line_count=10,
        language="python",
        quality=QualityInfo(),
    )


def _symbol_doc(rel_path: str) -> SymbolIndex:
    return SymbolIndex(
        document_id=f"symbol:{rel_path}:f",
        repo_id="owner/repo",
        file_path=rel_path,
        commit_hash="abc123",
        symbol_name="f",
        symbol_type="function",
        language="python",
        content="summary",
        start_line=1,
        end_line=5,
        quality=QualityInfo(),
    )


class ScriptedProcessor:
    """
    Stands in for FileProcessor. `script` maps a filename to a list of outcomes,
    one per attempt: "ok", "hang", "chunker_fail".
    """

    def __init__(self, script):
        self.script = {k: list(v) for k, v in script.items()}
        self.attempts = {}

    async def process(self, file_path, repo_path, repo_id, commit_hash,
                      parent_module_id, use_llm=None):
        rel = str(file_path.relative_to(repo_path))
        self.attempts[rel] = self.attempts.get(rel, 0) + 1
        outcomes = self.script.get(rel, ["ok"])
        outcome = outcomes.pop(0) if outcomes else "ok"

        if outcome == "hang":
            if use_llm is False:
                # The LLM-free reprocessing always completes — that is the whole
                # point of falling back to it.
                return ProcessedFile(file_index=_file_doc(rel), symbols=[_symbol_doc(rel)])
            await asyncio.sleep(30)  # far beyond the test budget
            raise AssertionError("should have been cancelled")

        if outcome == "chunker_fail":
            return ProcessedFile(
                file_index=_file_doc(rel),
                symbols=[_symbol_doc(rel)],
                failures=[FileFailure(rel, FailureKind.CHUNKER, "call failed")],
            )

        return ProcessedFile(file_index=_file_doc(rel), symbols=[_symbol_doc(rel)])


def _pipeline(script) -> V4Pipeline:
    """A V4Pipeline with only what process_files touches — __init__ loads models."""
    p = object.__new__(V4Pipeline)
    p.file_processor = ScriptedProcessor(script)
    p.quality_tracker = QualityTracker()
    p.file_failures = []
    return p


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=20))


REPO = Path("/repo")


class TestPerFileWallClock:
    def test_a_hung_file_is_degraded_not_dropped(self, monkeypatch):
        monkeypatch.setattr(config, "file_timeout_seconds", 0.1)
        pipeline = _pipeline({"slow.py": ["hang"]})

        result = _run(pipeline.process_file_bounded(
            REPO / "slow.py", REPO, "owner/repo", "abc123"
        ))

        assert result.file_index is not None, "the file must still reach the corpus"
        assert [f.kind for f in result.failures] == [FailureKind.TIMEOUT]

    def test_degraded_documents_are_marked_timeout_fallback(self, monkeypatch):
        """
        Distinct from an ordinary "fallback" so these stay findable afterwards
        without a DLQ lookup.
        """
        monkeypatch.setattr(config, "file_timeout_seconds", 0.1)
        pipeline = _pipeline({"slow.py": ["hang"]})

        result = _run(pipeline.process_file_bounded(
            REPO / "slow.py", REPO, "owner/repo", "abc123"
        ))

        sources = {d.quality.summary_source for d in result.documents}
        assert sources == {"timeout_fallback"}

    def test_the_fallback_runs_without_the_llm(self, monkeypatch):
        monkeypatch.setattr(config, "file_timeout_seconds", 0.1)
        pipeline = _pipeline({"slow.py": ["hang", "hang"]})

        _run(pipeline.process_file_bounded(REPO / "slow.py", REPO, "owner/repo", "abc123"))

        # Two attempts: the bounded one that hung, and the LLM-free reprocessing.
        assert pipeline.file_processor.attempts["slow.py"] == 2

    def test_a_healthy_file_is_untouched(self, monkeypatch):
        monkeypatch.setattr(config, "file_timeout_seconds", 5)
        pipeline = _pipeline({"fine.py": ["ok"]})

        result = _run(pipeline.process_file_bounded(
            REPO / "fine.py", REPO, "owner/repo", "abc123"
        ))

        assert not result.failed
        assert result.file_index is not None
        assert pipeline.file_processor.attempts["fine.py"] == 1


class TestEndOfPassRetry:
    def test_a_transient_failure_recovers_and_clears(self, monkeypatch):
        monkeypatch.setattr(config, "file_timeout_seconds", 5)
        monkeypatch.setattr(config, "retry_failed_files", True)
        pipeline = _pipeline({"flaky.py": ["chunker_fail", "ok"]})

        files, symbols, units = _run(pipeline.process_files(
            [REPO / "flaky.py"], REPO, "owner/repo", "abc123", concurrency=2
        ))

        assert pipeline.file_processor.attempts["flaky.py"] == 2
        assert pipeline.file_failures == [], "a recovered file must leave nothing behind"
        assert len(files) == 1

    def test_a_persistent_failure_is_kept_and_marked_retried(self, monkeypatch):
        monkeypatch.setattr(config, "file_timeout_seconds", 5)
        monkeypatch.setattr(config, "retry_failed_files", True)
        pipeline = _pipeline({"broken.py": ["chunker_fail", "chunker_fail"]})

        files, _, _ = _run(pipeline.process_files(
            [REPO / "broken.py"], REPO, "owner/repo", "abc123", concurrency=2
        ))

        assert len(pipeline.file_failures) == 1
        assert pipeline.file_failures[0].retried is True
        assert pipeline.file_failures[0].kind == FailureKind.CHUNKER
        # Still in the corpus. A poison file must not cost the repo its documents,
        # nor abort the repo — that would leave the commit unadvanced and every
        # later run would reprocess the whole repo over one file.
        assert len(files) == 1

    def test_retry_does_not_duplicate_documents(self, monkeypatch):
        monkeypatch.setattr(config, "file_timeout_seconds", 5)
        monkeypatch.setattr(config, "retry_failed_files", True)
        pipeline = _pipeline({"flaky.py": ["chunker_fail", "ok"], "fine.py": ["ok"]})

        files, symbols, _ = _run(pipeline.process_files(
            [REPO / "flaky.py", REPO / "fine.py"], REPO, "owner/repo", "abc123", concurrency=2
        ))

        assert len(files) == 2
        assert len(symbols) == 2
        assert len({d.document_id for d in files}) == 2

    def test_healthy_files_are_not_retried(self, monkeypatch):
        monkeypatch.setattr(config, "file_timeout_seconds", 5)
        monkeypatch.setattr(config, "retry_failed_files", True)
        pipeline = _pipeline({"a.py": ["ok"], "b.py": ["ok"]})

        _run(pipeline.process_files(
            [REPO / "a.py", REPO / "b.py"], REPO, "owner/repo", "abc123", concurrency=2
        ))

        assert pipeline.file_processor.attempts == {"a.py": 1, "b.py": 1}

    def test_retry_can_be_disabled(self, monkeypatch):
        monkeypatch.setattr(config, "file_timeout_seconds", 5)
        monkeypatch.setattr(config, "retry_failed_files", False)
        pipeline = _pipeline({"flaky.py": ["chunker_fail", "ok"]})

        _run(pipeline.process_files(
            [REPO / "flaky.py"], REPO, "owner/repo", "abc123", concurrency=2
        ))

        assert pipeline.file_processor.attempts["flaky.py"] == 1
        assert len(pipeline.file_failures) == 1
        assert pipeline.file_failures[0].retried is False


class TestFailuresAreCollected:
    def test_file_failures_is_reset_between_passes(self, monkeypatch):
        """Stale failures from a previous repo must not reach this repo's DLQ."""
        monkeypatch.setattr(config, "file_timeout_seconds", 5)
        monkeypatch.setattr(config, "retry_failed_files", False)
        pipeline = _pipeline({"broken.py": ["chunker_fail"]})

        _run(pipeline.process_files(
            [REPO / "broken.py"], REPO, "owner/repo", "abc123", concurrency=2
        ))
        assert len(pipeline.file_failures) == 1

        pipeline.file_processor = ScriptedProcessor({"clean.py": ["ok"]})
        _run(pipeline.process_files(
            [REPO / "clean.py"], REPO, "owner/repo", "abc123", concurrency=2
        ))
        assert pipeline.file_failures == []
