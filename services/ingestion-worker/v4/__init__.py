"""
CodeSmriti V4 Ingestion Pipeline

Key improvements over V3:
- Bottom-up summary aggregation (symbol -> file -> module -> repo)
- Correct symbol name extraction
- LLM chunker actually invoked for underchunked files
- Graceful degradation with quality flags
- Folder-based hierarchy
"""

from .schemas import (
    RepoSummary,
    ModuleSummary,
    FileIndex,
    SymbolIndex,
    SymbolRef,
    QualityInfo,
)
from .quality import QualityTracker, EnrichmentLevel
from .file_processor import FileProcessor
from .aggregator import BottomUpAggregator
from .llm_enricher import V4LLMEnricher
from .pipeline import V4Pipeline

__all__ = [
    "RepoSummary",
    "ModuleSummary",
    "FileIndex",
    "SymbolIndex",
    "SymbolRef",
    "QualityInfo",
    "QualityTracker",
    "EnrichmentLevel",
    "FileProcessor",
    "BottomUpAggregator",
    "V4LLMEnricher",
    "V4Pipeline",
]

SCHEMA_VERSION = "v4.0"
