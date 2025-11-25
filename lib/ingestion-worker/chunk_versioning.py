#!/usr/bin/env python3
"""
Chunk Versioning System

Tracks chunking/embedding "vintage" to:
1. Identify which pipeline version created a chunk
2. Prevent cron jobs from clobbering expensive LLM-enriched chunks
3. Support incremental migration between schema versions

Versioning scheme:
    v1.0 - Original chunking (full code stored, no hierarchy)
    v2.0 - Hierarchical chunking (repo → module → file → symbol)
    v3.0 - Normalized schema (summaries + line refs, no code storage)

Each chunk stores:
    - schema_version: "v3.0"
    - pipeline_version: "2025.11.24"
    - enrichment_level: "none" | "basic" | "llm_summary" | "llm_full"
    - enrichment_cost: estimated tokens used for LLM enrichment
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from enum import Enum


class SchemaVersion(str, Enum):
    """Chunk schema versions"""
    V1_ORIGINAL = "v1.0"      # Original: full code, flat structure
    V2_HIERARCHICAL = "v2.0"  # Hierarchical: still stores code
    V3_NORMALIZED = "v3.0"    # Normalized: summaries + line refs only


class EnrichmentLevel(str, Enum):
    """Level of LLM enrichment applied"""
    NONE = "none"              # No LLM processing
    BASIC = "basic"            # Tree-sitter parsing only
    LLM_SUMMARY = "llm_summary"  # LLM-generated summary
    LLM_FULL = "llm_full"      # Full LLM analysis (summary + purpose + usage)


@dataclass
class ChunkVersion:
    """Version metadata for a chunk"""
    schema_version: SchemaVersion
    pipeline_version: str  # Date-based: "2025.11.24"
    enrichment_level: EnrichmentLevel
    enrichment_cost: int  # Estimated tokens used
    created_at: str
    updated_at: Optional[str] = None

    # Protection flags
    protect_from_update: bool = False  # Don't overwrite with cron
    manual_edit: bool = False  # Was manually edited

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version.value,
            "pipeline_version": self.pipeline_version,
            "enrichment_level": self.enrichment_level.value,
            "enrichment_cost": self.enrichment_cost,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "protect_from_update": self.protect_from_update,
            "manual_edit": self.manual_edit
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChunkVersion":
        return cls(
            schema_version=SchemaVersion(data.get("schema_version", "v1.0")),
            pipeline_version=data.get("pipeline_version", "unknown"),
            enrichment_level=EnrichmentLevel(data.get("enrichment_level", "none")),
            enrichment_cost=data.get("enrichment_cost", 0),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at"),
            protect_from_update=data.get("protect_from_update", False),
            manual_edit=data.get("manual_edit", False)
        )


# Current versions
CURRENT_SCHEMA_VERSION = SchemaVersion.V3_NORMALIZED
CURRENT_PIPELINE_VERSION = datetime.now().strftime("%Y.%m.%d")


def create_version_metadata(
    enrichment_level: EnrichmentLevel = EnrichmentLevel.NONE,
    enrichment_cost: int = 0,
    protect: bool = False
) -> dict:
    """Create version metadata for a new chunk"""
    version = ChunkVersion(
        schema_version=CURRENT_SCHEMA_VERSION,
        pipeline_version=CURRENT_PIPELINE_VERSION,
        enrichment_level=enrichment_level,
        enrichment_cost=enrichment_cost,
        created_at=datetime.utcnow().isoformat(),
        protect_from_update=protect
    )
    return version.to_dict()


def should_update_chunk(existing_chunk: dict, new_commit: str) -> bool:
    """
    Determine if a chunk should be updated by the cron job.

    Returns False if:
    - Chunk has protect_from_update flag
    - Chunk has LLM enrichment (expensive to recreate)
    - Chunk is manually edited

    Returns True if:
    - Chunk has no version info (legacy)
    - Chunk is basic/none enrichment level
    - Commit hash changed AND not protected
    """
    version_data = existing_chunk.get("version", {})

    # Legacy chunks without version info - allow update
    if not version_data:
        return True

    version = ChunkVersion.from_dict(version_data)

    # Protected chunks - never update
    if version.protect_from_update:
        return False

    # Manually edited - never update
    if version.manual_edit:
        return False

    # LLM-enriched chunks - don't clobber expensive work
    if version.enrichment_level in [EnrichmentLevel.LLM_SUMMARY, EnrichmentLevel.LLM_FULL]:
        return False

    # Basic/none enrichment - safe to update
    return True


def estimate_enrichment_cost(content_length: int, enrichment_level: EnrichmentLevel) -> int:
    """Estimate token cost for enrichment"""
    if enrichment_level == EnrichmentLevel.NONE:
        return 0
    elif enrichment_level == EnrichmentLevel.BASIC:
        return 0  # Tree-sitter is free
    elif enrichment_level == EnrichmentLevel.LLM_SUMMARY:
        # Rough estimate: input tokens + output tokens
        input_tokens = content_length // 4  # ~4 chars per token
        output_tokens = 500  # Typical summary length
        return input_tokens + output_tokens
    elif enrichment_level == EnrichmentLevel.LLM_FULL:
        input_tokens = content_length // 4
        output_tokens = 1500  # Full analysis
        return input_tokens + output_tokens
    return 0


# Updated chunk schema with versioning
CHUNK_SCHEMA_V3 = """
{
    "chunk_id": "file:{repo_id}:{path}:{commit}",
    "type": "file_index",
    "repo_id": "kbhalerao/labcore",
    "file_path": "associates/role_privileges.py",

    # Search content (LLM-generated summary)
    "content": "...",
    "embedding": [...],

    # References (no code stored)
    "metadata": {
        "commit_hash": "abc123",
        "line_count": 245,
        "language": "python",
        "symbols": [...]
    },

    # Hierarchy
    "parent_id": "module:...",
    "children_ids": ["symbol:..."],

    # VERSIONING (NEW)
    "version": {
        "schema_version": "v3.0",
        "pipeline_version": "2025.11.24",
        "enrichment_level": "llm_full",
        "enrichment_cost": 2500,
        "created_at": "2025-11-24T20:00:00Z",
        "updated_at": null,
        "protect_from_update": true,
        "manual_edit": false
    }
}
"""


def print_version_info():
    """Print current version configuration"""
    print(f"""
CodeSmriti Chunk Versioning
===========================
Current Schema Version: {CURRENT_SCHEMA_VERSION.value}
Current Pipeline Version: {CURRENT_PIPELINE_VERSION}

Enrichment Levels:
  - none: No processing (legacy or skipped)
  - basic: Tree-sitter parsing only
  - llm_summary: LLM-generated summary
  - llm_full: Full LLM analysis

Update Protection:
  - protect_from_update: Prevents cron job from overwriting
  - LLM-enriched chunks are auto-protected
  - manual_edit flag for human edits
""")


if __name__ == "__main__":
    print_version_info()
