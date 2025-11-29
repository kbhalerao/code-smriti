"""
V4 Document Schemas

All documents use unified `document_id` field with format:
- repo:{repo_id}:{commit}
- module:{repo_id}:{path}:{commit}
- file:{repo_id}:{path}:{commit}
- symbol:{repo_id}:{path}:{name}:{commit}
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
from enum import Enum
import hashlib

SCHEMA_VERSION = "v4.0"
SYMBOL_MIN_LINES = 5  # Symbols >= this get their own document


class EnrichmentLevel(str, Enum):
    """Level of LLM enrichment for a document."""
    LLM_SUMMARY = "llm_summary"  # Full LLM-generated summary
    BASIC = "basic"              # Fallback: docstring + structure only
    NONE = "none"                # No summary available


@dataclass
class QualityInfo:
    """Quality tracking for a document."""
    enrichment_level: EnrichmentLevel = EnrichmentLevel.BASIC
    llm_available: bool = True
    summary_source: str = ""  # "llm_direct", "aggregated_from_*", "docstring", "fallback"
    enrichment_cost: int = 0  # Token estimate
    is_underchunked: bool = False
    underchunk_reason: str = ""
    llm_chunks_added: int = 0  # Number of semantic chunks found by LLM chunker

    def to_dict(self) -> Dict:
        return {
            "enrichment_level": self.enrichment_level.value,
            "llm_available": self.llm_available,
            "summary_source": self.summary_source,
            "enrichment_cost": self.enrichment_cost,
            "is_underchunked": self.is_underchunked,
            "underchunk_reason": self.underchunk_reason,
            "llm_chunks_added": self.llm_chunks_added,
        }


@dataclass
class VersionInfo:
    """Version tracking for a document."""
    schema_version: str = SCHEMA_VERSION
    pipeline_version: str = ""
    created_at: str = ""
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "schema_version": self.schema_version,
            "pipeline_version": self.pipeline_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class SymbolRef:
    """
    Reference to a symbol within a file.

    ALL symbols are listed in file_index.metadata.symbols.
    Only significant symbols (>= SYMBOL_MIN_LINES) get their own symbol_index doc.
    """
    name: str
    symbol_type: str  # "function", "class", "method"
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    methods: List[Dict] = field(default_factory=list)  # For classes: [{name, lines}]

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1

    @property
    def is_significant(self) -> bool:
        """Significant symbols get their own symbol_index document."""
        return self.line_count >= SYMBOL_MIN_LINES

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "type": self.symbol_type,
            "lines": [self.start_line, self.end_line],
            "significant": self.is_significant,
            "docstring": self.docstring,
            "methods": self.methods,
        }


@dataclass
class SymbolIndex:
    """
    V4 symbol_index document.

    Only created for significant symbols (>= 5 lines).
    Contains LLM-generated summary from docstring + code.
    """
    document_id: str  # symbol:{repo_id}:{path}:{name}:{commit}
    repo_id: str
    file_path: str
    commit_hash: str
    symbol_name: str
    symbol_type: str  # "function", "class", "method"

    # LLM-generated content
    content: str  # Summary for search
    embedding: Optional[List[float]] = None

    # Metadata
    start_line: int = 0
    end_line: int = 0
    docstring: Optional[str] = None
    methods: List[Dict] = field(default_factory=list)
    inherits: List[str] = field(default_factory=list)

    # Hierarchy
    parent_id: str = ""  # file:{repo_id}:{path}:{commit}

    # Quality & Version
    quality: QualityInfo = field(default_factory=QualityInfo)
    version: VersionInfo = field(default_factory=VersionInfo)

    # Internal: code snippet for embedding (not stored)
    _code_for_embedding: str = ""

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "symbol_index",
            "repo_id": self.repo_id,
            "file_path": self.file_path,
            "commit_hash": self.commit_hash,
            "symbol_name": self.symbol_name,
            "symbol_type": self.symbol_type,
            "content": self.content,
            "embedding": self.embedding,
            "metadata": {
                "start_line": self.start_line,
                "end_line": self.end_line,
                "line_count": self.end_line - self.start_line + 1,
                "docstring": self.docstring,
                "methods": self.methods,
                "inherits": self.inherits,
            },
            "parent_id": self.parent_id,
            "quality": self.quality.to_dict(),
            "version": self.version.to_dict(),
        }


@dataclass
class FileIndex:
    """
    V4 file_index document.

    Contains LLM-generated summary from chunk summaries + file content.
    Lists ALL symbols in metadata, but only significant ones have children docs.
    """
    document_id: str  # file:{repo_id}:{path}:{commit}
    repo_id: str
    file_path: str
    commit_hash: str

    # LLM-generated content
    content: str  # Summary for search
    embedding: Optional[List[float]] = None

    # Metadata
    line_count: int = 0
    language: str = "unknown"
    imports: List[str] = field(default_factory=list)
    symbols: List[SymbolRef] = field(default_factory=list)  # ALL symbols

    # Hierarchy
    parent_id: str = ""  # module:{repo_id}:{path}:{commit}
    children_ids: List[str] = field(default_factory=list)  # Only significant symbol docs

    # Quality & Version
    quality: QualityInfo = field(default_factory=QualityInfo)
    version: VersionInfo = field(default_factory=VersionInfo)

    # Internal: for embedding generation
    _embedding_text: str = ""

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "file_index",
            "repo_id": self.repo_id,
            "file_path": self.file_path,
            "commit_hash": self.commit_hash,
            "content": self.content,
            "embedding": self.embedding,
            "metadata": {
                "line_count": self.line_count,
                "language": self.language,
                "imports": self.imports,
                "symbols": [s.to_dict() for s in self.symbols],
            },
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "quality": self.quality.to_dict(),
            "version": self.version.to_dict(),
        }


@dataclass
class ModuleSummary:
    """
    V4 module_summary document.

    Represents a folder in the repo hierarchy.
    Contains LLM-generated summary aggregated from file summaries.
    """
    document_id: str  # module:{repo_id}:{path}:{commit}
    repo_id: str
    module_path: str  # Folder path relative to repo root
    commit_hash: str

    # LLM-generated content
    content: str  # Summary for search
    embedding: Optional[List[float]] = None

    # Metadata
    file_count: int = 0
    key_files: List[str] = field(default_factory=list)

    # Hierarchy
    parent_id: str = ""  # repo:{repo_id}:{commit} or module:{repo_id}:{parent_path}:{commit}
    children_ids: List[str] = field(default_factory=list)  # file or nested module docs

    # Quality & Version
    quality: QualityInfo = field(default_factory=QualityInfo)
    version: VersionInfo = field(default_factory=VersionInfo)

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "module_summary",
            "repo_id": self.repo_id,
            "module_path": self.module_path,
            "commit_hash": self.commit_hash,
            "content": self.content,
            "embedding": self.embedding,
            "metadata": {
                "file_count": self.file_count,
                "key_files": self.key_files,
            },
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "quality": self.quality.to_dict(),
            "version": self.version.to_dict(),
        }


@dataclass
class RepoSummary:
    """
    V4 repo_summary document.

    Top of hierarchy. Contains LLM-generated summary aggregated from module summaries.
    """
    document_id: str  # repo:{repo_id}:{commit}
    repo_id: str
    commit_hash: str

    # LLM-generated content
    content: str  # Summary for search
    embedding: Optional[List[float]] = None

    # Metadata
    total_files: int = 0
    total_lines: int = 0
    languages: Dict[str, int] = field(default_factory=dict)  # {lang: file_count}
    tech_stack: List[str] = field(default_factory=list)
    modules: List[str] = field(default_factory=list)  # Top-level module paths

    # Hierarchy
    children_ids: List[str] = field(default_factory=list)  # module docs

    # Quality & Version
    quality: QualityInfo = field(default_factory=QualityInfo)
    version: VersionInfo = field(default_factory=VersionInfo)

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "repo_summary",
            "repo_id": self.repo_id,
            "commit_hash": self.commit_hash,
            "content": self.content,
            "embedding": self.embedding,
            "metadata": {
                "total_files": self.total_files,
                "total_lines": self.total_lines,
                "languages": self.languages,
                "tech_stack": self.tech_stack,
                "modules": self.modules,
            },
            "children_ids": self.children_ids,
            "quality": self.quality.to_dict(),
            "version": self.version.to_dict(),
        }


# Helper functions for document ID generation (content-based hashing)

def _hash_id(key: str) -> str:
    """Generate SHA256 hash for document_id."""
    return hashlib.sha256(key.encode()).hexdigest()


def make_repo_id(repo_id: str, commit: str) -> str:
    """
    Generate document_id for repo_summary.

    Hash of: repo:{repo_id}:{commit}
    Same repo + commit = same ID (deduplication)
    """
    key = f"repo:{repo_id}:{commit[:12]}"
    return _hash_id(key)


def make_module_id(repo_id: str, module_path: str, commit: str) -> str:
    """
    Generate document_id for module_summary.

    Hash of: module:{repo_id}:{path}:{commit}
    """
    key = f"module:{repo_id}:{module_path}:{commit[:12]}"
    return _hash_id(key)


def make_file_id(repo_id: str, file_path: str, commit: str) -> str:
    """
    Generate document_id for file_index.

    Hash of: file:{repo_id}:{path}:{commit}
    """
    key = f"file:{repo_id}:{file_path}:{commit[:12]}"
    return _hash_id(key)


def make_symbol_id(repo_id: str, file_path: str, symbol_name: str, commit: str) -> str:
    """
    Generate document_id for symbol_index.

    Hash of: symbol:{repo_id}:{path}:{symbol_name}:{commit}
    """
    key = f"symbol:{repo_id}:{file_path}:{symbol_name}:{commit[:12]}"
    return _hash_id(key)
