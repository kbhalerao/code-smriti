"""
Data models for incremental updates.
"""

from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any


@dataclass
class ChangeSet:
    """Files changed between two commits"""
    added: List[str]
    modified: List[str]
    deleted: List[str]

    @property
    def total_changed(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)

    @property
    def files_to_process(self) -> List[str]:
        """Files that need processing (added + modified)"""
        return self.added + self.modified

    def __bool__(self):
        return self.total_changed > 0


# Valid status values for UpdateResult
STATUS_SKIPPED = 'skipped'        # No changes (commit unchanged)
STATUS_EXCLUDED = 'excluded'      # In exclusion list
STATUS_UPDATED = 'updated'        # Incremental update
STATUS_FULL_REINGEST = 'full_reingest'  # Threshold exceeded or new repo
STATUS_EMPTY = 'empty'            # Processed but 0 indexable files
STATUS_ERROR = 'error'            # Failed to process
STATUS_DELETED = 'deleted'        # Repo removed


@dataclass
class UpdateResult:
    """Result of processing a single repo"""
    repo_id: str
    status: str  # One of STATUS_* constants above
    reason: Optional[str] = None
    commit: Optional[str] = None  # The commit that was processed
    files_processed: int = 0
    files_deleted: int = 0
    docs_created: int = 0  # Number of documents created/updated
    error: Optional[str] = None
    duration_seconds: float = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for storage, omitting None values"""
        d = asdict(self)
        # Remove repo_id since it's used as the key in the parent dict
        del d['repo_id']
        # Remove None values to keep storage compact
        return {k: v for k, v in d.items() if v is not None}
