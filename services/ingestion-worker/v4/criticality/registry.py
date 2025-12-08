"""
Mother-Daughter Repository Registry

Defines which repos are "mother" repos (libraries) and their "daughter" repos
(consumers). This is used to identify cross-repo dependencies.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MotherRepo:
    """Configuration for a mother (library) repo."""

    repo_id: str  # e.g., "kbhalerao/labcore"
    pip_package: str  # Top-level import name, e.g., "labcore"
    path_markers: List[str] = field(default_factory=list)  # How to identify in pydeps paths
    daughters: List[str] = field(default_factory=list)  # Repos that depend on this

    def __post_init__(self):
        if not self.path_markers:
            # Default: look for package name in site-packages path
            self.path_markers = [
                f"/{self.pip_package}/",
                f"/site-packages/{self.pip_package}",
            ]


# Registry of known mother-daughter relationships
REPO_REGISTRY: Dict[str, MotherRepo] = {
    "kbhalerao/labcore": MotherRepo(
        repo_id="kbhalerao/labcore",
        pip_package="labcore",
        # Editable install: pip install -e at Docker entrypoint puts code in src/
        # Standard install: pip install puts in site-packages/
        path_markers=["/soildx-labcore/", "/labcore/", "site-packages/labcore"],
        daughters=[
            "kbhalerao/topsoil",
            "kbhalerao/pinionbe",
            "PeoplesCompany/farmworthdb",
            "ContinuumAgInc/topsoil2.0",
            "JessiePBhalerao/firstseedtests",
        ],
    ),
    "kbhalerao/agkit.io-backend": MotherRepo(
        repo_id="kbhalerao/agkit.io-backend",
        pip_package="agkit",
        path_markers=["/tier1apps/", "/tier2apps/", "/tier3apps/"],
        daughters=[],  # Currently no external consumers
    ),
}


def get_mother_repo(repo_id: str) -> Optional[MotherRepo]:
    """Get mother repo configuration if it exists."""
    return REPO_REGISTRY.get(repo_id)


def is_mother_repo(repo_id: str) -> bool:
    """Check if a repo is a mother repo."""
    return repo_id in REPO_REGISTRY


def get_daughters(mother_repo_id: str) -> List[str]:
    """Get list of daughter repos for a mother."""
    mother = get_mother_repo(mother_repo_id)
    return mother.daughters if mother else []


def identify_provider_repo(
    module_path: Optional[str],
    mother_repos: Optional[Dict[str, MotherRepo]] = None,
) -> Optional[str]:
    """
    Given a module's file path from pydeps, identify which mother repo it belongs to.

    Args:
        module_path: File path from pydeps output (e.g., "/path/to/site-packages/labcore/...")
        mother_repos: Registry to search (defaults to REPO_REGISTRY)

    Returns:
        repo_id if path matches a mother repo, None otherwise
    """
    if not module_path:
        return None

    if mother_repos is None:
        mother_repos = REPO_REGISTRY

    for repo_id, mother in mother_repos.items():
        for marker in mother.path_markers:
            if marker in module_path:
                return repo_id

    return None


def get_all_cluster_repos(mother_repo_id: str) -> List[str]:
    """
    Get all repos in a mother's cluster (mother + all daughters).

    Useful for scoping PageRank computation to a related set of repos.
    """
    mother = get_mother_repo(mother_repo_id)
    if not mother:
        return [mother_repo_id]

    return [mother_repo_id] + mother.daughters
