"""
Ingestion Runner - Handles locking, logging, and run history.

Wraps IncrementalUpdater with:
1. File-based lock to prevent overlapping runs
2. Rotating file logs
3. Ingestion run records in Couchbase
"""

import fcntl
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List
from dataclasses import dataclass, field, asdict

from loguru import logger

from .updater import IncrementalUpdater
from .models import UpdateResult


@dataclass
class IngestionRun:
    """Record of an ingestion run for history/monitoring."""
    run_id: str
    started_at: str
    completed_at: Optional[str] = None
    status: str = "running"  # running, completed, failed, interrupted
    trigger: str = "manual"  # manual, scheduled, webhook
    dry_run: bool = False

    # Stats
    repos_processed: int = 0
    repos_skipped: int = 0
    repos_excluded: int = 0
    repos_updated: int = 0
    repos_full_reingest: int = 0
    repos_empty: int = 0
    repos_cloned: int = 0
    repos_deleted: int = 0
    repos_error: int = 0
    files_processed: int = 0
    files_deleted: int = 0
    duration_seconds: float = 0.0

    # Errors
    errors: List[dict] = field(default_factory=list)

    # Per-repo details for the ingestion_run document
    repos: dict = field(default_factory=dict)

    def to_couchbase_doc(self) -> dict:
        """Convert to Couchbase document format (legacy ingestion_log)."""
        doc = asdict(self)
        doc["type"] = "ingestion_log"
        doc["document_id"] = f"ingestion_log:{self.run_id}"
        return doc

    def to_ingestion_run_doc(self) -> dict:
        """Convert to new ingestion_run document format with per-repo details."""
        return {
            "document_id": f"ingestion_run:{self.run_id}",
            "type": "ingestion_run",
            "run_id": self.run_id,
            "timestamp": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": self.duration_seconds,
            "trigger": self.trigger,
            "dry_run": self.dry_run,
            "status": self.status,
            "stats": {
                "processed": self.repos_processed,
                "skipped": self.repos_skipped,
                "excluded": self.repos_excluded,
                "updated": self.repos_updated,
                "full_reingest": self.repos_full_reingest,
                "empty": self.repos_empty,
                "deleted": self.repos_deleted,
                "error": self.repos_error,
                "files_processed": self.files_processed,
                "files_deleted": self.files_deleted,
            },
            "repos": self.repos,
            "errors": self.errors if self.errors else None,
        }


class LockError(Exception):
    """Raised when lock cannot be acquired."""
    pass


class IngestionRunner:
    """
    Runs incremental ingestion with proper locking and logging.

    Usage:
        runner = IngestionRunner()
        results = runner.run()  # Will fail if another run is active
    """

    LOCK_FILE = Path(__file__).parent.parent.parent / "logs" / "ingestion.lock"
    LOG_DIR = Path(__file__).parent.parent.parent / "logs"

    def __init__(
        self,
        threshold: float = 0.05,
        dry_run: bool = False,
        enable_llm: bool = True,
        llm_config=None,
        trigger: str = "manual"
    ):
        self.threshold = threshold
        self.dry_run = dry_run
        self.enable_llm = enable_llm
        self.llm_config = llm_config
        self.trigger = trigger

        self._lock_fd = None
        self._run_record: Optional[IngestionRun] = None

        # Ensure logs directory exists
        self.LOG_DIR.mkdir(parents=True, exist_ok=True)

    def _configure_logging(self, run_id: str):
        """Configure loguru with rotating file logs."""
        logger.remove()

        # Console output (brief)
        logger.add(
            sys.stderr,
            format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
            level="INFO"
        )

        # Main log file (rotating, detailed)
        logger.add(
            self.LOG_DIR / "incremental.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            level="INFO",
            rotation="10 MB",
            retention="30 days",
            compression="gz"
        )

        # Run-specific log file (for debugging specific runs)
        run_log = self.LOG_DIR / f"run_{run_id}.log"
        logger.add(
            run_log,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            level="INFO",
            retention="7 days"
        )

        # Error-only log (for quick issue detection)
        logger.add(
            self.LOG_DIR / "incremental.error.log",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            level="ERROR",
            rotation="5 MB",
            retention="30 days"
        )

        return run_log

    def _acquire_lock(self) -> bool:
        """
        Acquire exclusive file lock. Returns True if acquired.

        Uses flock() which is:
        - Automatically released on process exit/crash
        - Non-blocking with LOCK_NB flag
        """
        try:
            self._lock_fd = open(self.LOCK_FILE, 'w')
            fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Write PID and start time to lock file for debugging
            self._lock_fd.write(f"pid={os.getpid()}\n")
            self._lock_fd.write(f"started={datetime.now().isoformat()}\n")
            self._lock_fd.flush()

            return True

        except (IOError, OSError) as e:
            if self._lock_fd:
                self._lock_fd.close()
                self._lock_fd = None
            return False

    def _release_lock(self):
        """Release file lock and delete lock file."""
        if self._lock_fd:
            try:
                fcntl.flock(self._lock_fd.fileno(), fcntl.LOCK_UN)
                self._lock_fd.close()
                # Delete lock file to prevent stale lock confusion
                self.LOCK_FILE.unlink(missing_ok=True)
            except:
                pass
            finally:
                self._lock_fd = None

    def _get_running_info(self) -> Optional[dict]:
        """Get info about currently running ingestion (if any)."""
        if not self.LOCK_FILE.exists():
            return None

        try:
            content = self.LOCK_FILE.read_text()
            info = {}
            for line in content.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    info[key] = value
            return info
        except:
            return None

    def _regenerate_kpi(self):
        """Regenerate the KPI dashboard after ingestion."""
        try:
            import subprocess
            script_path = Path(__file__).parent.parent.parent / "scripts" / "generate_kpi.py"
            if script_path.exists():
                result = subprocess.run(
                    ["python", str(script_path)],
                    cwd=script_path.parent.parent,
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode == 0:
                    logger.info("KPI dashboard regenerated")
                else:
                    logger.warning(f"KPI generation failed: {result.stderr}")
        except Exception as e:
            logger.warning(f"Could not regenerate KPI dashboard: {e}")

    def _save_run_record(self, cb_client, repo_lifecycle=None):
        """Save run record to Couchbase and update commits index."""
        if self._run_record and not self.dry_run:
            try:
                # Save legacy ingestion_log document
                legacy_doc = self._run_record.to_couchbase_doc()
                cb_client.collection.upsert(legacy_doc["document_id"], legacy_doc)

                # Save new ingestion_run document with per-repo details
                run_doc = self._run_record.to_ingestion_run_doc()
                cb_client.collection.upsert(run_doc["document_id"], run_doc)
                logger.info(f"Saved ingestion_run document: {run_doc['document_id']}")

                # Update commits index with all processed repos
                if repo_lifecycle and self._run_record.repos:
                    commits = {}
                    for repo_id, repo_data in self._run_record.repos.items():
                        commit = repo_data.get('commit')
                        if commit:
                            commits[repo_id] = commit
                    if commits:
                        repo_lifecycle.update_commits_index(commits)
                        logger.info(f"Updated commits index for {len(commits)} repos")

            except Exception as e:
                logger.error(f"Failed to save run record: {e}")

    def run(self, repo_filter: Optional[str] = None) -> List[UpdateResult]:
        """
        Run incremental ingestion with locking.

        Raises:
            LockError: If another ingestion is already running
        """
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]

        # Try to acquire lock
        if not self._acquire_lock():
            running_info = self._get_running_info()
            if running_info:
                msg = f"Another ingestion is running (PID: {running_info.get('pid')}, started: {running_info.get('started')})"
            else:
                msg = "Another ingestion is running (could not read lock info)"
            raise LockError(msg)

        # Configure logging
        run_log = self._configure_logging(run_id)

        # Create run record
        self._run_record = IngestionRun(
            run_id=run_id,
            started_at=datetime.now().isoformat(),
            trigger=self.trigger,
            dry_run=self.dry_run
        )

        logger.info(f"Starting ingestion run: {run_id}")
        logger.info(f"Run log: {run_log}")

        start_time = datetime.now()
        results = []
        cb_client = None
        repo_lifecycle = None

        try:
            # Initialize updater
            updater = IncrementalUpdater(
                threshold=self.threshold,
                dry_run=self.dry_run,
                enable_llm=self.enable_llm,
                llm_config=self.llm_config
            )
            cb_client = updater.cb_client
            repo_lifecycle = updater.repo_lifecycle

            # Run the update
            results = updater.run(repo_filter=repo_filter)

            # Collect stats and build per-repo details
            for r in results:
                # Count by status
                if r.status == 'skipped':
                    self._run_record.repos_skipped += 1
                elif r.status == 'excluded':
                    self._run_record.repos_excluded += 1
                elif r.status == 'updated':
                    self._run_record.repos_updated += 1
                elif r.status == 'full_reingest':
                    self._run_record.repos_full_reingest += 1
                elif r.status == 'empty':
                    self._run_record.repos_empty += 1
                elif r.status == 'deleted':
                    self._run_record.repos_deleted += 1
                elif r.status == 'error':
                    self._run_record.repos_error += 1
                    self._run_record.errors.append({
                        "repo_id": r.repo_id,
                        "error": r.error
                    })

                self._run_record.files_processed += r.files_processed
                self._run_record.files_deleted += r.files_deleted

                # Store per-repo details (using to_dict which excludes repo_id)
                self._run_record.repos[r.repo_id] = r.to_dict()

            self._run_record.repos_processed = len(results)
            self._run_record.status = "completed" if self._run_record.repos_error == 0 else "completed_with_errors"

        except KeyboardInterrupt:
            logger.warning("Ingestion interrupted by user")
            self._run_record.status = "interrupted"
            raise

        except Exception as e:
            logger.exception(f"Ingestion failed: {e}")
            self._run_record.status = "failed"
            self._run_record.errors.append({"error": str(e)})
            raise

        finally:
            # Finalize run record
            self._run_record.completed_at = datetime.now().isoformat()
            self._run_record.duration_seconds = (datetime.now() - start_time).total_seconds()

            # Save to Couchbase and update commits index
            if cb_client:
                self._save_run_record(cb_client, repo_lifecycle)

            # Regenerate KPI dashboard
            self._regenerate_kpi()

            # Release lock
            self._release_lock()

            # Log summary
            logger.info(f"Run {run_id} {self._run_record.status} in {self._run_record.duration_seconds:.1f}s")

        return results


def check_running() -> Optional[dict]:
    """Check if an ingestion is currently running. Returns info dict or None."""
    lock_file = IngestionRunner.LOCK_FILE

    if not lock_file.exists():
        return None

    # Try to acquire lock (non-blocking)
    try:
        fd = open(lock_file, 'r')
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        # If we got here, no one is holding the lock
        fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        fd.close()
        return None
    except (IOError, OSError):
        # Lock is held - read info
        try:
            content = lock_file.read_text()
            info = {}
            for line in content.strip().split('\n'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    info[key] = value
            return info
        except:
            return {"status": "running", "details": "unknown"}
