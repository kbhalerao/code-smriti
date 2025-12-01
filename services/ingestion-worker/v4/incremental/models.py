"""
Data models for incremental updates.
"""

from dataclasses import dataclass
from typing import List, Optional


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


@dataclass
class UpdateResult:
    """Result of processing a single repo"""
    repo_id: str
    status: str  # 'skipped', 'updated', 'full_reingest', 'deleted', 'error'
    reason: Optional[str] = None
    files_processed: int = 0
    files_deleted: int = 0
    error: Optional[str] = None
    duration_seconds: float = 0
