"""
PageRank computation for criticality analysis.

Uses NetworkX to build a directed graph and compute PageRank scores.
Higher scores indicate modules that are more depended upon.
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

try:
    import networkx as nx
except ImportError:
    raise ImportError("NetworkX required. Install with: uv pip install networkx")

from .schemas import DependencyEdge, CriticalityInfo


def build_graph(edges: List[DependencyEdge]) -> nx.DiGraph:
    """
    Build directed graph from dependency edges.

    Edge direction: consumer → provider
    PageRank will give higher scores to nodes with many incoming edges (providers).

    Args:
        edges: List of DependencyEdge documents

    Returns:
        NetworkX directed graph
    """
    G = nx.DiGraph()

    for edge in edges:
        consumer_node = f"{edge.consumer_repo_id}:{edge.consumer_module}"
        provider_node = f"{edge.provider_repo_id}:{edge.provider_module}"

        # Add nodes with metadata
        if consumer_node not in G:
            G.add_node(
                consumer_node,
                repo_id=edge.consumer_repo_id,
                module=edge.consumer_module,
                file_path=edge.consumer_file_path,
            )
        if provider_node not in G:
            G.add_node(
                provider_node,
                repo_id=edge.provider_repo_id,
                module=edge.provider_module,
                file_path=edge.provider_file_path,
            )

        # Edge: consumer → provider (provider receives the "vote")
        G.add_edge(
            consumer_node,
            provider_node,
            edge_type=edge.edge_type,
            is_cross_repo=edge.is_cross_repo,
        )

    return G


def build_graph_from_pydeps(
    pydeps_data: dict,
    repo_id: str,
    project_prefixes: List[str],
) -> nx.DiGraph:
    """
    Build graph directly from pydeps output (faster than creating Edge objects first).

    Args:
        pydeps_data: Raw pydeps JSON output
        repo_id: Repository identifier
        project_prefixes: Module prefixes to include

    Returns:
        NetworkX directed graph
    """
    G = nx.DiGraph()

    def is_project_module(name: str) -> bool:
        return any(name.startswith(p) for p in project_prefixes)

    for module_name, info in pydeps_data.items():
        if not is_project_module(module_name):
            continue

        node_id = f"{repo_id}:{module_name}"
        if node_id not in G:
            G.add_node(
                node_id,
                repo_id=repo_id,
                module=module_name,
                file_path=info.get("path"),
            )

        for imported in info.get("imports", []):
            if is_project_module(imported):
                imported_node = f"{repo_id}:{imported}"
                imported_info = pydeps_data.get(imported, {})

                if imported_node not in G:
                    G.add_node(
                        imported_node,
                        repo_id=repo_id,
                        module=imported,
                        file_path=imported_info.get("path"),
                    )

                # Edge: module_name imports imported → module_name → imported
                G.add_edge(node_id, imported_node, edge_type="import", is_cross_repo=False)

    return G


def compute_pagerank(
    G: nx.DiGraph,
    alpha: float = 0.85,
) -> Dict[str, float]:
    """
    Compute PageRank scores.

    Args:
        G: NetworkX directed graph
        alpha: Damping factor (default 0.85, standard value)

    Returns:
        Dict mapping node_id → PageRank score (sorted descending)
    """
    if len(G) == 0:
        return {}

    scores = nx.pagerank(G, alpha=alpha)
    return dict(sorted(scores.items(), key=lambda x: -x[1]))


def compute_criticality_info(
    G: nx.DiGraph,
    scores: Dict[str, float],
    scope: str = "",
) -> Dict[str, CriticalityInfo]:
    """
    Compute full CriticalityInfo for each node.

    Args:
        G: NetworkX directed graph
        scores: PageRank scores from compute_pagerank
        scope: Description of computation scope (e.g., "your-org/platform-backend")

    Returns:
        Dict mapping node_id → CriticalityInfo
    """
    if not scores:
        return {}

    max_score = max(scores.values())
    sorted_scores = sorted(scores.values(), reverse=True)

    def get_percentile(score: float) -> int:
        """Compute percentile (0-100) for a score."""
        rank = sorted_scores.index(score) + 1
        return int(100 * (1 - rank / len(sorted_scores)))

    def get_downstream_repos(node: str) -> List[str]:
        """Get unique repos that depend on this node (predecessors in graph)."""
        repos = set()
        for pred in G.predecessors(node):
            pred_repo = G.nodes[pred].get("repo_id", "")
            if pred_repo:
                repos.add(pred_repo)
        return list(repos)

    results = {}
    for node, score in scores.items():
        results[node] = CriticalityInfo(
            score=score,
            normalized_score=score / max_score if max_score > 0 else 0,
            percentile=get_percentile(score),
            direct_dependents=G.in_degree(node),
            transitive_dependents=len(list(nx.ancestors(G, node))),
            downstream_repos=get_downstream_repos(node),
            in_degree=G.in_degree(node),
            out_degree=G.out_degree(node),
            scope=scope,
        )

    return results


@dataclass
class GraphAnalysis:
    """Results of graph analysis."""

    node_count: int
    edge_count: int
    isolated_count: int  # No connections
    root_count: int  # No outgoing edges, but has incoming (pure providers)
    leaf_count: int  # No incoming edges, but has outgoing (pure consumers)
    cross_repo_edges: int
    categories: Dict[str, Tuple[float, int]]  # category → (total_score, module_count)
    top_modules: List[Tuple[str, float, int, int]]  # (module, score, in_deg, out_deg)
    most_depended: List[Tuple[str, int, float]]  # (module, in_degree, score)


def analyze_graph(
    G: nx.DiGraph,
    scores: Dict[str, float],
    top_n: int = 30,
) -> GraphAnalysis:
    """
    Analyze the dependency graph structure.

    Args:
        G: NetworkX directed graph
        scores: PageRank scores
        top_n: Number of top modules to include in results

    Returns:
        GraphAnalysis with various metrics
    """
    # Count edge types
    cross_repo_edges = sum(
        1 for _, _, data in G.edges(data=True) if data.get("is_cross_repo", False)
    )

    # Node classifications
    isolated = [n for n in G.nodes() if G.degree(n) == 0]
    roots = [n for n in G.nodes() if G.out_degree(n) == 0 and G.in_degree(n) > 0]
    leaves = [n for n in G.nodes() if G.in_degree(n) == 0 and G.out_degree(n) > 0]

    # Group by category (top two levels of module path)
    categories = {}
    for node in G.nodes():
        module = G.nodes[node].get("module", node.split(":")[-1])
        parts = module.split(".")
        if len(parts) >= 2:
            category = f"{parts[0]}.{parts[1]}"
        else:
            category = parts[0]

        if category not in categories:
            categories[category] = (0.0, 0)

        total, count = categories[category]
        categories[category] = (total + scores.get(node, 0), count + 1)

    # Sort categories by total score
    categories = dict(sorted(categories.items(), key=lambda x: -x[1][0]))

    # Top modules by PageRank
    top_modules = [
        (node, score, G.in_degree(node), G.out_degree(node))
        for node, score in list(scores.items())[:top_n]
    ]

    # Most depended-upon (by in-degree)
    by_in_degree = sorted(G.nodes(), key=lambda n: G.in_degree(n), reverse=True)
    most_depended = [
        (node, G.in_degree(node), scores.get(node, 0)) for node in by_in_degree[:15]
    ]

    return GraphAnalysis(
        node_count=G.number_of_nodes(),
        edge_count=G.number_of_edges(),
        isolated_count=len(isolated),
        root_count=len(roots),
        leaf_count=len(leaves),
        cross_repo_edges=cross_repo_edges,
        categories=categories,
        top_modules=top_modules,
        most_depended=most_depended,
    )


def print_analysis(analysis: GraphAnalysis):
    """Print formatted analysis to stdout."""
    print(f"\n{'='*70}")
    print("DEPENDENCY GRAPH ANALYSIS")
    print(f"{'='*70}")
    print(f"Total modules: {analysis.node_count}")
    print(f"Total import edges: {analysis.edge_count}")
    print(f"Cross-repo edges: {analysis.cross_repo_edges}")
    print(f"Isolated modules: {analysis.isolated_count}")
    print(f"Root modules (providers only): {analysis.root_count}")
    print(f"Leaf modules (consumers only): {analysis.leaf_count}")

    print(f"\n{'='*70}")
    print(f"TOP {len(analysis.top_modules)} MOST CRITICAL MODULES (by PageRank)")
    print(f"{'='*70}")
    print(f"{'Rank':<5} {'Module':<55} {'Score':<8} {'In':<5} {'Out':<5}")
    print("-" * 70)

    for i, (module, score, in_deg, out_deg) in enumerate(analysis.top_modules, 1):
        # Extract just the module name (after repo_id:)
        display = module.split(":")[-1] if ":" in module else module
        if len(display) > 54:
            display = display[:51] + "..."
        print(f"{i:<5} {display:<55} {score:.4f}   {in_deg:<5} {out_deg:<5}")

    print(f"\n{'='*70}")
    print("CATEGORY CRITICALITY (sum of PageRank)")
    print(f"{'='*70}")
    for cat, (total_score, count) in list(analysis.categories.items())[:15]:
        print(f"  {cat}: {total_score:.4f} ({count} modules)")

    print(f"\n{'='*70}")
    print("MOST DEPENDED-UPON (by in-degree)")
    print(f"{'='*70}")
    for module, in_deg, score in analysis.most_depended:
        display = module.split(":")[-1] if ":" in module else module
        print(f"  {display}: {in_deg} dependents (PageRank: {score:.4f})")
