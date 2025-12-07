"""
Criticality Analysis Schemas

Document types:
- dependency_edge: A dependency from consumer → provider
- criticality_score: Optional standalone doc (or embedded in symbol_index/file_index)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
import hashlib

from ..schemas import VersionInfo, SCHEMA_VERSION


def _hash_id(key: str) -> str:
    """Generate SHA256 hash for document_id."""
    return hashlib.sha256(key.encode()).hexdigest()


def make_edge_id(
    consumer_repo: str,
    consumer_module: str,
    provider_repo: str,
    provider_module: str,
) -> str:
    """
    Generate document_id for dependency_edge.

    Hash of: edge:{consumer_repo}:{consumer_module}→{provider_repo}:{provider_module}
    """
    key = f"edge:{consumer_repo}:{consumer_module}→{provider_repo}:{provider_module}"
    return _hash_id(key)


@dataclass
class DependencyEdge:
    """
    Represents a dependency from consumer → provider.

    Edge semantics: consumer imports/uses provider.
    For PageRank: edges point TO providers (they receive "votes").
    """

    # Required fields (no defaults) - must come first
    consumer_repo_id: str
    consumer_module: str  # e.g., "tier1apps.foundations.models"
    provider_repo_id: str
    provider_module: str  # e.g., "tier1apps.core.base"

    # Optional fields (with defaults)
    document_id: str = ""  # SHA256 hash of edge key, auto-generated if empty
    consumer_file_path: Optional[str] = None  # Resolved file path if known
    provider_file_path: Optional[str] = None  # Resolved file path if known
    edge_type: str = "import"  # "import", "inherit", "call"
    is_cross_repo: bool = False  # True if consumer_repo != provider_repo
    source: str = "pydeps"  # "pydeps", "tree-sitter", "manual"
    version: VersionInfo = field(default_factory=VersionInfo)

    def __post_init__(self):
        if not self.document_id:
            self.document_id = make_edge_id(
                self.consumer_repo_id,
                self.consumer_module,
                self.provider_repo_id,
                self.provider_module,
            )
        self.is_cross_repo = self.consumer_repo_id != self.provider_repo_id

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "dependency_edge",
            "consumer_repo_id": self.consumer_repo_id,
            "consumer_module": self.consumer_module,
            "consumer_file_path": self.consumer_file_path,
            "provider_repo_id": self.provider_repo_id,
            "provider_module": self.provider_module,
            "provider_file_path": self.provider_file_path,
            "edge_type": self.edge_type,
            "is_cross_repo": self.is_cross_repo,
            "source": self.source,
            "version": self.version.to_dict(),
        }


@dataclass
class CriticalityInfo:
    """
    Criticality score for a module/symbol.

    Can be embedded in existing symbol_index/file_index docs,
    or stored as a standalone criticality_score document.
    """

    score: float  # PageRank score (raw, not normalized)
    normalized_score: float = 0.0  # Score / max_score (0.0 - 1.0)
    percentile: int = 0  # 0-100, where 100 = most critical

    # Dependency metrics
    direct_dependents: int = 0  # Modules that directly import this
    transitive_dependents: int = 0  # All reachable dependents (ancestors in graph)
    downstream_repos: List[str] = field(default_factory=list)  # Repos that depend on this

    # Graph position
    in_degree: int = 0  # Number of incoming edges (dependents)
    out_degree: int = 0  # Number of outgoing edges (dependencies)

    # Computation metadata
    last_computed: str = ""
    scope: str = ""  # Which repos were included in computation

    def __post_init__(self):
        if not self.last_computed:
            self.last_computed = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict:
        return {
            "score": self.score,
            "normalized_score": self.normalized_score,
            "percentile": self.percentile,
            "direct_dependents": self.direct_dependents,
            "transitive_dependents": self.transitive_dependents,
            "downstream_repos": self.downstream_repos,
            "in_degree": self.in_degree,
            "out_degree": self.out_degree,
            "last_computed": self.last_computed,
            "scope": self.scope,
        }


@dataclass
class CriticalityScore:
    """
    Standalone criticality_score document.

    Alternative to embedding CriticalityInfo in existing docs.
    Useful for cross-repo analysis where module may not have a file_index doc.
    """

    document_id: str  # crit:{repo_id}:{module}
    repo_id: str
    module_name: str  # Python module path (e.g., "tier1apps.foundations.models")

    criticality: CriticalityInfo = field(default_factory=CriticalityInfo)
    version: VersionInfo = field(default_factory=VersionInfo)

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "criticality_score",
            "repo_id": self.repo_id,
            "module_name": self.module_name,
            "criticality": self.criticality.to_dict(),
            "version": self.version.to_dict(),
        }


def make_criticality_id(repo_id: str, module_name: str) -> str:
    """Generate document_id for criticality_score."""
    key = f"crit:{repo_id}:{module_name}"
    return _hash_id(key)
