#!/usr/bin/env python3
"""
POC: Compute PageRank criticality scores from pydeps output.

Usage:
    # Single file, single prefix:
    uv run python scripts/poc_criticality.py t1deps.json tier1apps

    # Multiple files, multiple prefixes (merge mode):
    uv run python scripts/poc_criticality.py t1deps.json,t2deps.json,t3deps.json tier1apps,tier2apps,tier3apps

    # Or just specify prefixes to include (auto-detect from files):
    uv run python scripts/poc_criticality.py deps.json tier1apps,tier2apps,tier3apps
"""

import json
import sys
from pathlib import Path

try:
    import networkx as nx
except ImportError:
    print("NetworkX not installed. Run: uv pip install networkx")
    sys.exit(1)


def load_pydeps(filepath: str) -> dict:
    """Load pydeps JSON output."""
    with open(filepath) as f:
        return json.load(f)


def load_multiple_pydeps(filepaths: list[str]) -> dict:
    """Load and merge multiple pydeps JSON files."""
    merged = {}
    for filepath in filepaths:
        data = load_pydeps(filepath.strip())
        # Merge - later files override earlier for duplicate keys
        merged.update(data)
    return merged


def build_graph(deps: dict, project_prefixes: list[str]) -> nx.DiGraph:
    """
    Build directed graph from pydeps output.

    Edge direction: A imports B → edge from A to B
    PageRank will give higher scores to nodes with many incoming edges (B).

    Args:
        deps: pydeps JSON data
        project_prefixes: list of prefixes to include (e.g., ["tier1apps", "tier2apps"])
    """
    G = nx.DiGraph()

    def is_project_module(name: str) -> bool:
        return any(name.startswith(p) for p in project_prefixes)

    for module_name, info in deps.items():
        # Only include modules from our project
        if not is_project_module(module_name):
            continue

        G.add_node(module_name)

        for imported in info.get("imports", []):
            if is_project_module(imported):
                # A imports B: edge A → B
                G.add_edge(module_name, imported)

    return G


def compute_pagerank(G: nx.DiGraph) -> dict:
    """Compute PageRank scores."""
    if len(G) == 0:
        return {}

    # alpha=0.85 is the standard damping factor
    scores = nx.pagerank(G, alpha=0.85)
    return dict(sorted(scores.items(), key=lambda x: -x[1]))


def analyze_graph(G: nx.DiGraph, scores: dict, top_n: int = 30):
    """Print analysis of the dependency graph."""
    print(f"\n{'='*70}")
    print(f"DEPENDENCY GRAPH ANALYSIS")
    print(f"{'='*70}")
    print(f"Total modules: {G.number_of_nodes()}")
    print(f"Total import edges: {G.number_of_edges()}")

    # Find isolated nodes (no imports, not imported)
    isolated = [n for n in G.nodes() if G.degree(n) == 0]
    print(f"Isolated modules: {len(isolated)}")

    # Find root modules (imported but don't import others in project)
    roots = [n for n in G.nodes() if G.out_degree(n) == 0 and G.in_degree(n) > 0]
    print(f"Root modules (no internal deps, but depended upon): {len(roots)}")

    # Find leaf modules (import others but not imported)
    leaves = [n for n in G.nodes() if G.in_degree(n) == 0 and G.out_degree(n) > 0]
    print(f"Leaf modules (depend on others, not depended upon): {len(leaves)}")

    print(f"\n{'='*70}")
    print(f"TOP {top_n} MOST CRITICAL MODULES (by PageRank)")
    print(f"{'='*70}")
    print(f"{'Rank':<5} {'Module':<55} {'Score':<8} {'In-Deg':<7} {'Out-Deg':<7}")
    print("-" * 70)

    for i, (module, score) in enumerate(list(scores.items())[:top_n], 1):
        in_deg = G.in_degree(module)
        out_deg = G.out_degree(module)
        # Truncate module name if too long
        display_name = module if len(module) <= 54 else module[:51] + "..."
        print(f"{i:<5} {display_name:<55} {score:.4f}   {in_deg:<7} {out_deg:<7}")

    # Show modules by category
    print(f"\n{'='*70}")
    print("MODULES BY CATEGORY (top 10 each)")
    print(f"{'='*70}")

    # Group by top-level package
    categories = {}
    for module in G.nodes():
        parts = module.split(".")
        if len(parts) >= 2:
            category = f"{parts[0]}.{parts[1]}"
        else:
            category = parts[0]
        if category not in categories:
            categories[category] = []
        categories[category].append((module, scores.get(module, 0)))

    # Sort categories by total criticality
    category_scores = {
        cat: sum(s for _, s in modules)
        for cat, modules in categories.items()
    }
    sorted_cats = sorted(category_scores.items(), key=lambda x: -x[1])

    print(f"\nCategory criticality (sum of PageRank scores):")
    for cat, total_score in sorted_cats[:15]:
        module_count = len(categories[cat])
        print(f"  {cat}: {total_score:.4f} ({module_count} modules)")

    # Show the most depended-upon modules (highest in-degree)
    print(f"\n{'='*70}")
    print("MOST DEPENDED-UPON MODULES (by in-degree)")
    print(f"{'='*70}")
    by_in_degree = sorted(G.nodes(), key=lambda n: G.in_degree(n), reverse=True)
    for module in by_in_degree[:15]:
        in_deg = G.in_degree(module)
        score = scores.get(module, 0)
        print(f"  {module}: {in_deg} dependents (PageRank: {score:.4f})")


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <pydeps_json> [project_prefixes]")
        print(f"")
        print(f"Examples:")
        print(f"  {sys.argv[0]} t1deps.json tier1apps")
        print(f"  {sys.argv[0]} t1deps.json,t2deps.json tier1apps,tier2apps")
        print(f"  {sys.argv[0]} deps.json tier1apps,tier2apps,tier3apps")
        sys.exit(1)

    filepaths = sys.argv[1].split(",")
    prefixes_arg = sys.argv[2] if len(sys.argv) > 2 else "tier1apps"
    project_prefixes = [p.strip() for p in prefixes_arg.split(",")]

    print(f"Loading {len(filepaths)} file(s): {filepaths}")
    if len(filepaths) > 1:
        deps = load_multiple_pydeps(filepaths)
    else:
        deps = load_pydeps(filepaths[0])
    print(f"Loaded {len(deps)} total modules (including external)")

    print(f"\nBuilding graph for prefixes: {project_prefixes}")
    G = build_graph(deps, project_prefixes)

    print(f"Computing PageRank...")
    scores = compute_pagerank(G)

    analyze_graph(G, scores)

    # Optional: export to file
    output_file = Path(filepaths[0]).stem + "_criticality.json"
    with open(output_file, "w") as f:
        json.dump({
            "project_prefixes": project_prefixes,
            "node_count": G.number_of_nodes(),
            "edge_count": G.number_of_edges(),
            "scores": {k: round(v, 6) for k, v in scores.items()}
        }, f, indent=2)
    print(f"\nScores exported to {output_file}")


if __name__ == "__main__":
    main()
