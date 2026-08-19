"""
V4 Document Schemas

All documents use a unified `document_id` derived from *where the thing lives*,
never from the commit it was last seen at:
- repo:{repo_id}
- module:{repo_id}:{path}
- file:{repo_id}:{path}
- symbol:{repo_id}:{path}:{name}
- semantic:{repo_id}:{path}:{start}-{end}

Identity deliberately excludes the commit. Keying on commit made re-ingestion an
*insert* rather than an upsert, so every full re-ingest minted a fresh generation
of every document and orphaned the previous one — one file was found carrying five
generations and 26 documents for its 12 functions. Superseding a document is now
the storage engine's job (same key, same doc) instead of something every write
path has to remember to clean up after itself.

`commit_hash` survives as a *field*: it records when this version was last
processed, which is what the RAG layer displays and what incremental uses to skip
unchanged work. `content_hash` records what was actually hashed, so a change can
be detected without re-deriving identity.

Documents still have to be removed when the thing they describe stops existing —
a renamed or deleted symbol. That is reconciliation against the current parse
(see `file_processor.reconcile_file_children`), not generational cleanup.
"""

from dataclasses import dataclass, field
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
    document_id: str  # symbol:{repo_id}:{path}:{name}
    repo_id: str
    file_path: str
    commit_hash: str  # commit this version was processed at — a field, not identity
    symbol_name: str
    symbol_type: str  # "function", "class", "method"

    # LLM-generated content
    content: str  # Summary for search
    language: str = ""  # Programming language
    embedding: Optional[List[float]] = None

    # Metadata
    start_line: int = 0
    end_line: int = 0
    docstring: Optional[str] = None
    methods: List[Dict] = field(default_factory=list)
    inherits: List[str] = field(default_factory=list)
    content_hash: str = ""  # digest of the source this summary was derived from

    # Hierarchy
    parent_id: str = ""  # file:{repo_id}:{path}

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
            "language": self.language,
            "content": self.content,
            "embedding": self.embedding,
            "metadata": {
                "start_line": self.start_line,
                "end_line": self.end_line,
                "line_count": self.end_line - self.start_line + 1,
                "docstring": self.docstring,
                "methods": self.methods,
                "inherits": self.inherits,
                "content_hash": self.content_hash,
            },
            "parent_id": self.parent_id,
            "quality": self.quality.to_dict(),
            "version": self.version.to_dict(),
        }


@dataclass
class SemanticUnit:
    """
    V4 semantic_unit document — a region of a file the LLM chunker identified.

    Kept deliberately separate from `symbol_index`. These are not symbols: the
    chunker names regions rather than parsing them, and those names are neither
    stable across runs nor guaranteed to exist in the file. One test file was
    indexed under `RealstackAPI.get_users` / `get_cities` — the names of the
    client methods it *tests*, none of which are defined there. Mixing that into
    the symbol table makes the symbol table untrustworthy for every consumer.

    So the LLM's name is stored as `label`, never `symbol_name`, and identity
    comes from the line span it pointed at.
    """
    document_id: str  # semantic:{repo_id}:{path}:{start}-{end}
    repo_id: str
    file_path: str
    commit_hash: str
    label: str  # the LLM's name for this region — descriptive, NOT an identifier
    unit_type: str  # embedded_sql, business_logic, api_endpoint, data_transform, …

    content: str  # Summary for search
    language: str = ""
    embedding: Optional[List[float]] = None

    start_line: int = 0
    end_line: int = 0
    purpose: str = ""
    related_symbols: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    confidence: float = 0.0
    content_hash: str = ""

    parent_id: str = ""  # file:{repo_id}:{path}

    quality: QualityInfo = field(default_factory=QualityInfo)
    version: VersionInfo = field(default_factory=VersionInfo)

    _code_for_embedding: str = ""

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "semantic_unit",
            "repo_id": self.repo_id,
            "file_path": self.file_path,
            "commit_hash": self.commit_hash,
            "label": self.label,
            "unit_type": self.unit_type,
            "language": self.language,
            "content": self.content,
            "embedding": self.embedding,
            "metadata": {
                "start_line": self.start_line,
                "end_line": self.end_line,
                "line_count": self.end_line - self.start_line + 1,
                "purpose": self.purpose,
                "related_symbols": self.related_symbols,
                "tags": self.tags,
                "confidence": self.confidence,
                "content_hash": self.content_hash,
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
    document_id: str  # file:{repo_id}:{path}
    repo_id: str
    file_path: str
    commit_hash: str  # commit this version was processed at — a field, not identity

    # LLM-generated content
    content: str  # Summary for search
    embedding: Optional[List[float]] = None

    # Metadata
    line_count: int = 0
    language: str = "unknown"
    imports: List[str] = field(default_factory=list)
    symbols: List[SymbolRef] = field(default_factory=list)  # ALL symbols
    content_hash: str = ""  # digest of the file content this summary describes

    # Hierarchy
    parent_id: str = ""  # module:{repo_id}:{path}
    children_ids: List[str] = field(default_factory=list)  # symbol + semantic_unit docs

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
                "content_hash": self.content_hash,
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
    document_id: str  # module:{repo_id}:{path}
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
    parent_id: str = ""  # repo:{repo_id} or module:{repo_id}:{parent_path}
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
    document_id: str  # repo:{repo_id}
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


@dataclass
class RepoBDR:
    """
    V4 repo_bdr document - Business Development Representative brief.

    Generated from repo_summary + README content.
    Updated weekly or when inputs change significantly.
    """
    document_id: str  # bdr:{repo_id}
    repo_id: str

    # BDR content
    content: str  # The BDR brief (markdown)
    reasoning_trace: Optional[str] = None  # Model's thinking process

    # Change detection
    input_hash: str = ""  # Hash of (repo_summary + readme) to detect changes
    source_commit: str = ""  # Commit hash when generated
    last_checked: str = ""  # ISO timestamp of last check (for snooze logic)

    # Embedding for keyword/prospect query matching
    embedding: Optional[List[float]] = None

    # Generation metadata
    model: str = ""  # Model used (e.g., nvidia/nemotron-3-nano)
    generation_tokens: int = 0
    reasoning_tokens: int = 0

    # Version info
    version: VersionInfo = field(default_factory=VersionInfo)

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "repo_bdr",
            "repo_id": self.repo_id,
            "content": self.content,
            "reasoning_trace": self.reasoning_trace,
            "input_hash": self.input_hash,
            "source_commit": self.source_commit,
            "last_checked": self.last_checked,
            "embedding": self.embedding,
            "metadata": {
                "model": self.model,
                "generation_tokens": self.generation_tokens,
                "reasoning_tokens": self.reasoning_tokens,
            },
            "version": self.version.to_dict(),
        }


# Helper functions for document ID generation (content-based hashing)

def _hash_id(key: str) -> str:
    """Generate SHA256 hash for document_id."""
    return hashlib.sha256(key.encode()).hexdigest()


def make_repo_id(repo_id: str) -> str:
    """
    Generate document_id for repo_summary.

    Hash of: repo:{repo_id}  — one document per repository, forever.
    """
    return _hash_id(f"repo:{repo_id}")


def make_module_id(repo_id: str, module_path: str) -> str:
    """
    Generate document_id for module_summary.

    Hash of: module:{repo_id}:{path}  — one document per folder.
    """
    return _hash_id(f"module:{repo_id}:{module_path}")


def make_file_id(repo_id: str, file_path: str) -> str:
    """
    Generate document_id for file_index.

    Hash of: file:{repo_id}:{path}  — one document per file.
    """
    return _hash_id(f"file:{repo_id}:{file_path}")


def make_symbol_id(repo_id: str, file_path: str, symbol_name: str) -> str:
    """
    Generate document_id for symbol_index.

    Hash of: symbol:{repo_id}:{path}:{symbol_name}  — one document per symbol.

    Renaming a symbol produces a new identity, which is correct: the old name no
    longer exists and its document is removed by reconciliation, not superseded.
    """
    return _hash_id(f"symbol:{repo_id}:{file_path}:{symbol_name}")


def make_semantic_unit_id(repo_id: str, file_path: str, start_line: int, end_line: int) -> str:
    """
    Generate document_id for semantic_unit (LLM-identified region).

    Hash of: semantic:{repo_id}:{path}:{start}-{end}

    Keyed on the line span rather than the LLM's label, because the label is not
    stable: the same region has come back as `fema_api_response_validation` on one
    run and `fema_response_validation` on the next. The span is what the model
    actually pointed at, so it is the only part of its output fit to be identity.
    """
    return _hash_id(f"semantic:{repo_id}:{file_path}:{start_line}-{end_line}")


def make_content_hash(text: str) -> str:
    """Short digest of the source a document was derived from, for change detection."""
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def make_bdr_id(repo_id: str) -> str:
    """
    Generate document_id for repo_bdr.

    Hash of: bdr:{repo_id}
    Note: Not commit-specific since BDR is more stable than code summaries.
    """
    key = f"bdr:{repo_id}"
    return _hash_id(key)


def make_bdr_input_hash(repo_summary: str, readme_content: str) -> str:
    """
    Generate hash of BDR inputs to detect when regeneration is needed.

    Uses first 1000 chars of each to avoid false positives from minor changes.
    """
    key = f"{repo_summary[:1000]}|{readme_content[:1000]}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
