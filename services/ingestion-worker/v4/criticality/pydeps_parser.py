"""
Parse pydeps JSON output into DependencyEdge documents.

pydeps output format:
{
    "module.name": {
        "name": "module.name",
        "path": "/abs/path/to/module.py",
        "imports": ["other.module", "another.module"],
        "imported_by": ["consumer.module"],
        "bacon": 2  # distance from target
    }
}
"""

import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from .schemas import DependencyEdge, make_edge_id
from .registry import identify_provider_repo, MotherRepo


def load_pydeps_json(filepath: str) -> dict:
    """Load pydeps JSON output from file."""
    with open(filepath) as f:
        return json.load(f)


def load_multiple_pydeps(filepaths: List[str]) -> dict:
    """Load and merge multiple pydeps JSON files."""
    merged = {}
    for filepath in filepaths:
        data = load_pydeps_json(filepath.strip())
        merged.update(data)
    return merged


def parse_pydeps_intra_repo(
    pydeps_data: dict,
    repo_id: str,
    project_prefixes: List[str],
) -> List[DependencyEdge]:
    """
    Parse pydeps output for intra-repo (within same project) dependencies.

    Args:
        pydeps_data: Raw pydeps JSON output
        repo_id: Repository identifier (e.g., "kbhalerao/agkit.io-backend")
        project_prefixes: Module prefixes to include (e.g., ["tier1apps", "tier2apps"])

    Returns:
        List of DependencyEdge documents for edges within the project
    """
    edges = []

    def is_project_module(name: str) -> bool:
        return any(name.startswith(p) for p in project_prefixes)

    for module_name, info in pydeps_data.items():
        if not is_project_module(module_name):
            continue

        consumer_path = info.get("path")

        for imported in info.get("imports", []):
            if not is_project_module(imported):
                continue

            # Get provider path if available
            provider_info = pydeps_data.get(imported, {})
            provider_path = provider_info.get("path")

            edge = DependencyEdge(
                consumer_repo_id=repo_id,
                consumer_module=module_name,
                provider_repo_id=repo_id,
                provider_module=imported,
                consumer_file_path=consumer_path,
                provider_file_path=provider_path,
            )
            edges.append(edge)

    return edges


def parse_pydeps_cross_repo(
    pydeps_data: dict,
    daughter_repo_id: str,
    daughter_prefixes: List[str],
    mother_repos: Optional[Dict[str, MotherRepo]] = None,
) -> Tuple[List[DependencyEdge], List[DependencyEdge]]:
    """
    Parse pydeps output for cross-repo (daughter → mother) dependencies.

    This requires running pydeps in a daughter repo's environment where
    the mother repo is pip-installed. The path field tells us which
    modules come from the mother.

    Args:
        pydeps_data: Raw pydeps JSON output (from daughter repo)
        daughter_repo_id: e.g., "kbhalerao/topsoil"
        daughter_prefixes: Module prefixes for the daughter (e.g., ["topsoil"])
        mother_repos: Registry of mother repos to identify

    Returns:
        Tuple of (cross_repo_edges, intra_repo_edges)
    """
    from .registry import REPO_REGISTRY

    if mother_repos is None:
        mother_repos = REPO_REGISTRY

    cross_edges = []
    intra_edges = []

    def is_daughter_module(name: str) -> bool:
        return any(name.startswith(p) for p in daughter_prefixes)

    for module_name, info in pydeps_data.items():
        module_path = info.get("path", "")

        # Determine if this module is from a mother repo
        provider_repo = identify_provider_repo(module_path, mother_repos)

        if provider_repo:
            # This is a mother module - check who imports it from the daughter
            for importer in info.get("imported_by", []):
                importer_info = pydeps_data.get(importer, {})
                importer_path = importer_info.get("path", "")

                # Check if importer is from the daughter (not another mother module)
                importer_provider = identify_provider_repo(importer_path, mother_repos)

                if importer_provider is None and is_daughter_module(importer):
                    # This is a cross-repo edge: daughter imports mother
                    edge = DependencyEdge(
                        consumer_repo_id=daughter_repo_id,
                        consumer_module=importer,
                        provider_repo_id=provider_repo,
                        provider_module=module_name,
                        consumer_file_path=importer_path,
                        provider_file_path=module_path,
                    )
                    cross_edges.append(edge)
        else:
            # Not a mother module - if it's a daughter module, track internal deps
            if is_daughter_module(module_name):
                for imported in info.get("imports", []):
                    imported_info = pydeps_data.get(imported, {})
                    imported_path = imported_info.get("path", "")
                    imported_provider = identify_provider_repo(imported_path, mother_repos)

                    if imported_provider is None and is_daughter_module(imported):
                        # Intra-repo edge within daughter
                        edge = DependencyEdge(
                            consumer_repo_id=daughter_repo_id,
                            consumer_module=module_name,
                            provider_repo_id=daughter_repo_id,
                            provider_module=imported,
                            consumer_file_path=module_path,
                            provider_file_path=imported_path,
                        )
                        intra_edges.append(edge)

    return cross_edges, intra_edges
