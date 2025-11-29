"""
V4 Quality Tracking

Tracks enrichment levels, LLM availability, and processing metrics.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from enum import Enum
import time


class EnrichmentLevel(str, Enum):
    """Level of LLM enrichment for a document."""
    LLM_SUMMARY = "llm_summary"  # Full LLM-generated summary
    BASIC = "basic"              # Fallback: docstring + structure only
    NONE = "none"                # No summary available


@dataclass
class ProcessingStats:
    """Statistics for a single processing run."""
    start_time: float = 0
    end_time: float = 0

    files_processed: int = 0
    files_failed: int = 0
    files_skipped: int = 0  # Already existed (deduplication)

    symbols_processed: int = 0
    modules_created: int = 0

    llm_calls: int = 0
    llm_successes: int = 0
    llm_failures: int = 0
    llm_tokens_used: int = 0

    embeddings_generated: int = 0

    errors: List[Dict] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        if self.end_time and self.start_time:
            return self.end_time - self.start_time
        return 0

    @property
    def llm_success_rate(self) -> float:
        if self.llm_calls == 0:
            return 1.0
        return self.llm_successes / self.llm_calls

    def to_dict(self) -> Dict:
        return {
            "duration_seconds": round(self.duration_seconds, 2),
            "files": {
                "processed": self.files_processed,
                "failed": self.files_failed,
                "skipped": self.files_skipped,
            },
            "symbols_processed": self.symbols_processed,
            "modules_created": self.modules_created,
            "llm": {
                "calls": self.llm_calls,
                "successes": self.llm_successes,
                "failures": self.llm_failures,
                "success_rate": round(self.llm_success_rate, 3),
                "tokens_used": self.llm_tokens_used,
            },
            "embeddings_generated": self.embeddings_generated,
            "errors": self.errors[:10],  # Limit to first 10
        }


class CircuitBreaker:
    """
    Circuit breaker for LLM calls.

    Opens after consecutive failures, preventing further calls until reset.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 60.0
    ):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.consecutive_failures = 0
        self.last_failure_time: Optional[float] = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        """Check if circuit is open (blocking calls)."""
        if not self._is_open:
            return False

        # Check if reset timeout has passed
        if self.last_failure_time:
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.reset_timeout:
                self._is_open = False
                self.consecutive_failures = 0
                return False

        return True

    def record_success(self):
        """Record a successful call."""
        self.consecutive_failures = 0
        self._is_open = False

    def record_failure(self):
        """Record a failed call."""
        self.consecutive_failures += 1
        self.last_failure_time = time.time()

        if self.consecutive_failures >= self.failure_threshold:
            self._is_open = True

    def reset(self):
        """Manually reset the circuit breaker."""
        self.consecutive_failures = 0
        self._is_open = False
        self.last_failure_time = None


class QualityTracker:
    """
    Tracks quality metrics across an ingestion run.

    Provides:
    - Processing statistics
    - LLM circuit breaker
    - Error collection
    """

    def __init__(
        self,
        circuit_breaker_threshold: int = 5,
        circuit_breaker_reset: float = 60.0
    ):
        self.stats = ProcessingStats()
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=circuit_breaker_threshold,
            reset_timeout=circuit_breaker_reset
        )
        self._current_repo: Optional[str] = None

    def start_run(self, repo_id: str):
        """Start tracking a new ingestion run."""
        self._current_repo = repo_id
        self.stats = ProcessingStats()
        self.stats.start_time = time.time()

    def end_run(self):
        """End the current ingestion run."""
        self.stats.end_time = time.time()

    def record_file_processed(self):
        """Record a successfully processed file."""
        self.stats.files_processed += 1

    def record_file_failed(self, file_path: str, error: str):
        """Record a failed file."""
        self.stats.files_failed += 1
        self.stats.errors.append({
            "file": file_path,
            "error": error,
            "timestamp": datetime.now().isoformat()
        })

    def record_file_skipped(self):
        """Record a skipped file (already exists)."""
        self.stats.files_skipped += 1

    def record_symbol_processed(self):
        """Record a processed symbol."""
        self.stats.symbols_processed += 1

    def record_module_created(self):
        """Record a created module summary."""
        self.stats.modules_created += 1

    def record_llm_call(self, success: bool, tokens: int = 0):
        """Record an LLM call result."""
        self.stats.llm_calls += 1
        self.stats.llm_tokens_used += tokens

        if success:
            self.stats.llm_successes += 1
            self.circuit_breaker.record_success()
        else:
            self.stats.llm_failures += 1
            self.circuit_breaker.record_failure()

    def record_embedding(self):
        """Record an embedding generation."""
        self.stats.embeddings_generated += 1

    @property
    def llm_available(self) -> bool:
        """Check if LLM is available (circuit breaker not open)."""
        return not self.circuit_breaker.is_open

    def get_summary(self) -> Dict:
        """Get summary of the current run."""
        return {
            "repo_id": self._current_repo,
            "llm_available": self.llm_available,
            "stats": self.stats.to_dict()
        }

    def print_summary(self):
        """Print a human-readable summary."""
        s = self.stats
        print("\n" + "=" * 60)
        print("V4 INGESTION SUMMARY")
        print("=" * 60)
        print(f"  Repo: {self._current_repo}")
        print(f"  Duration: {s.duration_seconds:.1f}s")
        print()
        print("  Files:")
        print(f"    Processed: {s.files_processed}")
        print(f"    Failed: {s.files_failed}")
        print(f"    Skipped: {s.files_skipped}")
        print()
        print(f"  Symbols: {s.symbols_processed}")
        print(f"  Modules: {s.modules_created}")
        print()
        print("  LLM:")
        print(f"    Calls: {s.llm_calls}")
        print(f"    Success rate: {s.llm_success_rate:.1%}")
        print(f"    Tokens: {s.llm_tokens_used:,}")
        print(f"    Available: {self.llm_available}")
        print()
        print(f"  Embeddings: {s.embeddings_generated}")
        if s.errors:
            print()
            print(f"  Errors ({len(s.errors)}):")
            for e in s.errors[:5]:
                print(f"    - {e['file']}: {e['error'][:50]}")
        print("=" * 60)
