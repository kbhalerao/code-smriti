#!/usr/bin/env python3
"""
CLI for criticality analysis.

Usage:
    # Analyze intra-repo dependencies (single repo)
    python -m v4.criticality.cli analyze \
        --repo kbhalerao/agkit.io-backend \
        --pydeps t1deps.json,t2deps.json,t3deps.json \
        --prefixes tier1apps,tier2apps,tier3apps

    # Export criticality scores to JSON
    python -m v4.criticality.cli analyze \
        --repo kbhalerao/agkit.io-backend \
        --pydeps t1deps.json \
        --prefixes tier1apps \
        --output criticality.json

    # Show graph statistics only
    python -m v4.criticality.cli stats \
        --pydeps t1deps.json \
        --prefixes tier1apps
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List

from .pydeps_parser import load_pydeps_json, load_multiple_pydeps
from .pagerank import (
    build_graph_from_pydeps,
    compute_pagerank,
    compute_criticality_info,
    analyze_graph,
    print_analysis,
)


def cmd_analyze(args):
    """Analyze dependencies and compute criticality scores."""
    # Parse file list
    pydeps_files = [f.strip() for f in args.pydeps.split(",")]
    prefixes = [p.strip() for p in args.prefixes.split(",")]

    print(f"Loading {len(pydeps_files)} pydeps file(s)...")
    if len(pydeps_files) > 1:
        pydeps_data = load_multiple_pydeps(pydeps_files)
    else:
        pydeps_data = load_pydeps_json(pydeps_files[0])

    print(f"Loaded {len(pydeps_data)} total modules (including external)")
    print(f"Filtering to prefixes: {prefixes}")

    # Build graph
    G = build_graph_from_pydeps(pydeps_data, args.repo, prefixes)
    print(f"Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # Compute PageRank
    scores = compute_pagerank(G)

    # Analyze and print
    analysis = analyze_graph(G, scores, top_n=args.top)
    print_analysis(analysis)

    # Export if requested
    if args.output:
        criticality = compute_criticality_info(G, scores, scope=args.repo)

        output_data = {
            "repo_id": args.repo,
            "prefixes": prefixes,
            "pydeps_files": pydeps_files,
            "stats": {
                "node_count": analysis.node_count,
                "edge_count": analysis.edge_count,
                "cross_repo_edges": analysis.cross_repo_edges,
            },
            "scores": {
                node: {
                    "module": node.split(":")[-1],
                    "score": info.score,
                    "normalized_score": info.normalized_score,
                    "percentile": info.percentile,
                    "in_degree": info.in_degree,
                    "out_degree": info.out_degree,
                }
                for node, info in criticality.items()
            },
            "categories": {
                cat: {"total_score": score, "module_count": count}
                for cat, (score, count) in analysis.categories.items()
            },
        }

        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"\nExported to {args.output}")


def cmd_stats(args):
    """Show graph statistics without full analysis."""
    pydeps_files = [f.strip() for f in args.pydeps.split(",")]
    prefixes = [p.strip() for p in args.prefixes.split(",")]

    if len(pydeps_files) > 1:
        pydeps_data = load_multiple_pydeps(pydeps_files)
    else:
        pydeps_data = load_pydeps_json(pydeps_files[0])

    G = build_graph_from_pydeps(pydeps_data, "analysis", prefixes)

    print(f"Nodes: {G.number_of_nodes()}")
    print(f"Edges: {G.number_of_edges()}")
    print(f"Density: {G.number_of_edges() / (G.number_of_nodes() ** 2):.6f}")

    # Degree distribution
    in_degrees = [G.in_degree(n) for n in G.nodes()]
    out_degrees = [G.out_degree(n) for n in G.nodes()]

    print(f"\nIn-degree: min={min(in_degrees)}, max={max(in_degrees)}, avg={sum(in_degrees)/len(in_degrees):.2f}")
    print(f"Out-degree: min={min(out_degrees)}, max={max(out_degrees)}, avg={sum(out_degrees)/len(out_degrees):.2f}")


def cmd_compare(args):
    """Compare criticality between two pydeps snapshots."""
    print("Compare command not yet implemented")
    print("Would compare scores between two versions to find:")
    print("  - New critical modules")
    print("  - Modules that became less critical")
    print("  - Changed dependency relationships")


def main():
    parser = argparse.ArgumentParser(
        description="Criticality analysis for Python codebases",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # analyze command
    analyze_parser = subparsers.add_parser("analyze", help="Analyze dependencies")
    analyze_parser.add_argument(
        "--repo",
        required=True,
        help="Repository ID (e.g., kbhalerao/agkit.io-backend)",
    )
    analyze_parser.add_argument(
        "--pydeps",
        required=True,
        help="Comma-separated pydeps JSON files",
    )
    analyze_parser.add_argument(
        "--prefixes",
        required=True,
        help="Comma-separated module prefixes to include",
    )
    analyze_parser.add_argument(
        "--top",
        type=int,
        default=30,
        help="Number of top modules to show (default: 30)",
    )
    analyze_parser.add_argument(
        "--output",
        help="Export criticality scores to JSON file",
    )
    analyze_parser.set_defaults(func=cmd_analyze)

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show graph statistics")
    stats_parser.add_argument("--pydeps", required=True, help="pydeps JSON file(s)")
    stats_parser.add_argument("--prefixes", required=True, help="Module prefixes")
    stats_parser.set_defaults(func=cmd_stats)

    # compare command
    compare_parser = subparsers.add_parser("compare", help="Compare two snapshots")
    compare_parser.add_argument("--before", required=True, help="Before JSON")
    compare_parser.add_argument("--after", required=True, help="After JSON")
    compare_parser.set_defaults(func=cmd_compare)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
