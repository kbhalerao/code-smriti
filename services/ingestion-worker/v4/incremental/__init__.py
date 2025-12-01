"""
Incremental V4 Update Module

Git-based incremental updates for the V4 ingestion pipeline.
"""

from .models import ChangeSet, UpdateResult
from .updater import IncrementalUpdater

__all__ = ['IncrementalUpdater', 'ChangeSet', 'UpdateResult']
