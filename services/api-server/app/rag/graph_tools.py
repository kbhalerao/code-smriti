"""
Dependency Graph Tools

Query tools for dependency graphs stored in Couchbase.
Used to find affected tests, get criticality info, and explore dependencies.
"""

from typing import List, Dict, Optional
from pydantic import BaseModel
from loguru import logger

from app.database.couchbase_client import CouchbaseClient


# =============================================================================
# Response Models
# =============================================================================

class AffectedTestsResult(BaseModel):
    """Result from affected_tests query."""
    changed_modules: List[str]
    affected_modules: List[str]
    tests_to_run: List[str]
    cluster_id: str
    graph_found: bool


class CriticalityResult(BaseModel):
    """Criticality info for a module."""
    module: str
    repo_id: str
    score: float
    percentile: int
    in_degree: int
    out_degree: int
    direct_dependents: List[str]
    is_test: bool


class GraphInfo(BaseModel):
    """Summary info about a dependency graph."""
    cluster_id: str
    total_nodes: int
    total_edges: int
    cross_repo_edges: int
    repos: Dict[str, dict]
    computed_at: str


# =============================================================================
# Graph Loading
# =============================================================================

async def load_graph(
    db: CouchbaseClient,
    cluster_id: str,
    tenant_id: str = "code_kosha"
) -> Optional[dict]:
    """
    Load dependency graph from Couchbase.

    Args:
        db: Couchbase client
        cluster_id: Mother repo ID (e.g., "kbhalerao/labcore")
        tenant_id: Bucket name

    Returns:
        Graph document or None if not found
    """
    doc_id = f"depgraph:{cluster_id}"
    try:
        bucket = db.cluster.bucket(tenant_id)
        collection = bucket.default_collection()
        result = collection.get(doc_id)
        return result.content_as[dict]
    except Exception as e:
        logger.warning(f"Graph not found: {doc_id} - {e}")
        return None


# =============================================================================
# Query Tools
# =============================================================================

async def get_graph_info(
    db: CouchbaseClient,
    cluster_id: str,
    tenant_id: str = "code_kosha"
) -> Optional[GraphInfo]:
    """
    Get summary info about a dependency graph.

    Args:
        db: Couchbase client
        cluster_id: Mother repo ID
        tenant_id: Bucket name

    Returns:
        GraphInfo or None if not found
    """
    graph = await load_graph(db, cluster_id, tenant_id)
    if not graph:
        return None

    metadata = graph.get("metadata", {})
    return GraphInfo(
        cluster_id=cluster_id,
        total_nodes=metadata.get("total_nodes", 0),
        total_edges=metadata.get("total_edges", 0),
        cross_repo_edges=metadata.get("cross_repo_edges", 0),
        repos=graph.get("repos", {}),
        computed_at=metadata.get("computed_at", "unknown"),
    )


async def find_affected_tests(
    db: CouchbaseClient,
    changed_files: List[str],
    cluster_id: str,
    tenant_id: str = "code_kosha"
) -> AffectedTestsResult:
    """
    Given changed files, find all tests that should run.

    Uses BFS on the reverse dependency graph to find all modules that
    transitively depend on the changed modules, then filters to tests.

    Args:
        db: Couchbase client
        changed_files: List of file paths (e.g., ["common/models/__init__.py"])
        cluster_id: Mother repo ID (e.g., "kbhalerao/labcore")
        tenant_id: Bucket name

    Returns:
        AffectedTestsResult with tests to run
    """
    graph = await load_graph(db, cluster_id, tenant_id)
    if not graph:
        return AffectedTestsResult(
            changed_modules=[],
            affected_modules=[],
            tests_to_run=["*"],  # Run all if no graph
            cluster_id=cluster_id,
            graph_found=False,
        )

    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])

    # Build file path to node mapping
    file_to_node = {}
    for node_id, info in nodes.items():
        file_path = info.get("file_path")
        if file_path:
            # Normalize path for matching - strip common prefixes
            normalized = file_path
            for prefix in ["/site-packages/", "/src/", "/FarmWorthDB/"]:
                if prefix in normalized:
                    normalized = normalized.split(prefix)[-1]
            file_to_node[normalized] = node_id
            # Also add the original path
            file_to_node[file_path] = node_id

    # Match changed files to nodes
    changed_modules = []
    for changed_file in changed_files:
        # Try exact match first
        if changed_file in file_to_node:
            changed_modules.append(file_to_node[changed_file])
            continue

        # Try suffix matching
        for fp, node_id in file_to_node.items():
            if changed_file.endswith(fp) or fp.endswith(changed_file):
                changed_modules.append(node_id)
                break
        else:
            # Try module name matching (e.g., "common.models" from "common/models/__init__.py")
            module_guess = changed_file.replace("/", ".").replace("__init__.py", "").rstrip(".")
            module_guess = module_guess.replace(".py", "")
            for node_id, info in nodes.items():
                if info.get("module", "").endswith(module_guess) or module_guess.endswith(info.get("module", "")):
                    changed_modules.append(node_id)
                    break

    if not changed_modules:
        logger.info(f"No modules found for changed files: {changed_files}")
        return AffectedTestsResult(
            changed_modules=[],
            affected_modules=[],
            tests_to_run=[],
            cluster_id=cluster_id,
            graph_found=True,
        )

    # Build reverse adjacency (provider → consumers)
    # edges are [consumer, provider] pairs
    reverse_adj: Dict[str, List[str]] = {}
    for consumer, provider in edges:
        if provider not in reverse_adj:
            reverse_adj[provider] = []
        reverse_adj[provider].append(consumer)

    # BFS to find all affected modules
    affected = set(changed_modules)
    queue = list(changed_modules)
    while queue:
        node = queue.pop(0)
        for consumer in reverse_adj.get(node, []):
            if consumer not in affected:
                affected.add(consumer)
                queue.append(consumer)

    # Separate tests from non-tests
    tests = []
    affected_non_tests = []
    for node_id in affected:
        info = nodes.get(node_id, {})
        if info.get("is_test"):
            tests.append(info.get("module", node_id))
        elif node_id not in changed_modules:
            affected_non_tests.append(info.get("module", node_id))

    # Get module names for changed modules
    changed_module_names = [
        nodes.get(n, {}).get("module", n) for n in changed_modules
    ]

    logger.info(
        f"affected_tests: {len(changed_modules)} changed -> "
        f"{len(affected)} affected -> {len(tests)} tests"
    )

    return AffectedTestsResult(
        changed_modules=changed_module_names,
        affected_modules=sorted(affected_non_tests),
        tests_to_run=sorted(set(tests)),
        cluster_id=cluster_id,
        graph_found=True,
    )


async def get_criticality(
    db: CouchbaseClient,
    module: str,
    cluster_id: str,
    tenant_id: str = "code_kosha"
) -> Optional[CriticalityResult]:
    """
    Get criticality info for a specific module.

    Args:
        db: Couchbase client
        module: Module name (e.g., "common.models")
        cluster_id: Mother repo ID
        tenant_id: Bucket name

    Returns:
        CriticalityResult or None if not found
    """
    graph = await load_graph(db, cluster_id, tenant_id)
    if not graph:
        return None

    nodes = graph.get("nodes", {})
    edges = graph.get("edges", [])

    # Find node by module name (partial match supported)
    target_node_id = None
    target_info = None
    for node_id, info in nodes.items():
        if info.get("module") == module:
            target_node_id = node_id
            target_info = info
            break
        elif module in info.get("module", ""):
            # Partial match - take first
            if target_node_id is None:
                target_node_id = node_id
                target_info = info

    if not target_info:
        logger.info(f"Module not found in graph: {module}")
        return None

    # Find direct dependents (modules that import this one)
    dependents = []
    for consumer, provider in edges:
        if provider == target_node_id:
            consumer_info = nodes.get(consumer, {})
            dependents.append(consumer_info.get("module", consumer))

    return CriticalityResult(
        module=target_info.get("module", module),
        repo_id=target_info.get("repo_id", ""),
        score=target_info.get("criticality", 0.0),
        percentile=target_info.get("percentile", 0),
        in_degree=target_info.get("in_degree", 0),
        out_degree=target_info.get("out_degree", 0),
        direct_dependents=dependents[:20],  # Limit for display
        is_test=target_info.get("is_test", False),
    )


async def list_clusters(
    db: CouchbaseClient,
    tenant_id: str = "code_kosha"
) -> List[str]:
    """
    List all available dependency graph clusters.

    Returns:
        List of cluster IDs that have stored graphs
    """
    try:
        n1ql = f"""
            SELECT cluster_id, metadata.total_nodes, metadata.computed_at
            FROM `{tenant_id}`
            WHERE type = 'dependency_graph'
            ORDER BY metadata.computed_at DESC
        """
        result = db.cluster.query(n1ql)
        return [row["cluster_id"] for row in result]
    except Exception as e:
        logger.error(f"Failed to list clusters: {e}")
        return []
