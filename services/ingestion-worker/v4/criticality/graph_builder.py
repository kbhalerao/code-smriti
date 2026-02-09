"""
Build dependency graph from pydeps output and store in Couchbase.

This module provides both CLI and programmatic interfaces for building
dependency graphs from pydeps JSON output.

Usage (CLI):
    uv run python -m v4.criticality.graph_builder \
        --cluster your-org/core-library \
        --pydeps /path/to/deps.json \
        --daughter-repo client-org/client-app \
        --daughter-prefixes listings,fwcma,mapbinder

Usage (programmatic):
    from v4.criticality.graph_builder import build_and_store_graph
    result = build_and_store_graph(
        pydeps_json_path="/path/to/deps.json",
        cluster_id="your-org/core-library",
        daughter_repo_id="client-org/client-app",
        daughter_prefixes=["listings", "fwcma"],
    )
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Dict, Optional

from .pydeps_parser import load_pydeps_json, parse_pydeps_cross_repo
from .pagerank import build_graph, compute_pagerank, compute_criticality_info
from .registry import REPO_REGISTRY, identify_provider_repo
from .schemas import DependencyEdge


@dataclass
class BuildGraphResult:
    """Result of building a dependency graph."""
    success: bool
    cluster_id: str
    nodes: int
    edges: int
    cross_repo_edges: int
    message: str
    document: Optional[dict] = None


def build_graph_document(
    cluster_id: str,
    pydeps_files: List[str],
    daughter_repo_id: str,
    daughter_prefixes: List[str],
) -> dict:
    """
    Build the dependency graph document from pydeps output.

    Args:
        cluster_id: Mother repo ID (e.g., "your-org/core-library")
        pydeps_files: List of paths to pydeps JSON files
        daughter_repo_id: Daughter repo ID (e.g., "client-org/client-app")
        daughter_prefixes: Module prefixes for daughter (e.g., ["listings", "fwcma"])

    Returns:
        Document dict ready for storage in Couchbase
    """
    # Load and merge pydeps files
    pydeps_data = {}
    for f in pydeps_files:
        with open(f) as fp:
            pydeps_data.update(json.load(fp))

    # Parse cross-repo and intra-repo edges
    cross_edges, intra_daughter_edges = parse_pydeps_cross_repo(
        pydeps_data,
        daughter_repo_id=daughter_repo_id,
        daughter_prefixes=daughter_prefixes,
        mother_repos=REPO_REGISTRY,
    )

    # Find mother modules and build intra-mother edges
    mother_modules = set()
    intra_mother_edges = []

    for module_name, info in pydeps_data.items():
        path = info.get("path") or ""
        provider_repo = identify_provider_repo(path, REPO_REGISTRY)
        if provider_repo == cluster_id:
            mother_modules.add(module_name)

    for module_name in mother_modules:
        info = pydeps_data.get(module_name, {})
        for imported in info.get("imports", []):
            if imported in mother_modules:
                edge = DependencyEdge(
                    consumer_repo_id=cluster_id,
                    consumer_module=module_name,
                    provider_repo_id=cluster_id,
                    provider_module=imported,
                )
                intra_mother_edges.append(edge)

    # Combine all edges and build graph
    all_edges = cross_edges + intra_daughter_edges + intra_mother_edges
    G = build_graph(all_edges)
    scores = compute_pagerank(G)
    criticality = compute_criticality_info(G, scores, scope=cluster_id)

    # Build nodes dict
    nodes = {}
    for node_id, info in criticality.items():
        repo_id, module = node_id.split(":", 1)
        is_test = "test" in module.lower()

        # Get file path from pydeps if available
        file_path = None
        if module in pydeps_data:
            file_path = pydeps_data[module].get("path")

        nodes[node_id] = {
            "repo_id": repo_id,
            "module": module,
            "file_path": file_path,
            "criticality": round(info.score, 6),
            "percentile": info.percentile,
            "in_degree": info.in_degree,
            "out_degree": info.out_degree,
            "is_test": is_test,
        }

    # Build edges list
    edges = [
        [
            f"{e.consumer_repo_id}:{e.consumer_module}",
            f"{e.provider_repo_id}:{e.provider_module}",
        ]
        for e in all_edges
    ]

    # Build repos summary
    repos = {}
    for node_id in nodes:
        repo_id = node_id.split(":")[0]
        if repo_id not in repos:
            repos[repo_id] = {
                "role": "mother" if repo_id == cluster_id else "daughter",
                "module_count": 0,
            }
        repos[repo_id]["module_count"] += 1

    return {
        "document_id": f"depgraph:{cluster_id}",
        "type": "dependency_graph",
        "cluster_id": cluster_id,
        "nodes": nodes,
        "edges": edges,
        "repos": repos,
        "metadata": {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "pydeps_sources": pydeps_files,
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "cross_repo_edges": len(cross_edges),
        },
    }


def build_and_store_graph(
    pydeps_json_path: str,
    cluster_id: str,
    daughter_repo_id: str,
    daughter_prefixes: List[str],
    dry_run: bool = False,
) -> BuildGraphResult:
    """
    Build dependency graph from pydeps JSON and store in Couchbase.

    This is the main entry point for programmatic use.

    Args:
        pydeps_json_path: Path to pydeps JSON file
        cluster_id: Mother repo ID (e.g., "your-org/core-library")
        daughter_repo_id: Daughter repo ID (e.g., "client-org/client-app")
        daughter_prefixes: Module prefixes for daughter
        dry_run: If True, don't store, just return the document

    Returns:
        BuildGraphResult with success status and metadata
    """
    try:
        doc = build_graph_document(
            cluster_id=cluster_id,
            pydeps_files=[pydeps_json_path],
            daughter_repo_id=daughter_repo_id,
            daughter_prefixes=daughter_prefixes,
        )

        if dry_run:
            return BuildGraphResult(
                success=True,
                cluster_id=cluster_id,
                nodes=doc["metadata"]["total_nodes"],
                edges=doc["metadata"]["total_edges"],
                cross_repo_edges=doc["metadata"]["cross_repo_edges"],
                message=f"[DRY RUN] Would store: {doc['document_id']}",
                document=doc,
            )

        # Import here to avoid circular imports and allow dry-run without DB
        from storage.couchbase_client import CouchbaseClient

        client = CouchbaseClient()
        client.collection.upsert(doc["document_id"], doc)

        return BuildGraphResult(
            success=True,
            cluster_id=cluster_id,
            nodes=doc["metadata"]["total_nodes"],
            edges=doc["metadata"]["total_edges"],
            cross_repo_edges=doc["metadata"]["cross_repo_edges"],
            message=f"Stored graph: {doc['document_id']}",
            document=doc,
        )

    except Exception as e:
        return BuildGraphResult(
            success=False,
            cluster_id=cluster_id,
            nodes=0,
            edges=0,
            cross_repo_edges=0,
            message=f"Error: {str(e)}",
        )


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build dependency graph from pydeps output"
    )
    parser.add_argument(
        "--cluster",
        required=True,
        help="Mother repo ID (e.g., your-org/core-library)",
    )
    parser.add_argument(
        "--pydeps",
        required=True,
        help="Path to pydeps JSON file",
    )
    parser.add_argument(
        "--daughter-repo",
        required=True,
        help="Daughter repo ID (e.g., client-org/client-app)",
    )
    parser.add_argument(
        "--daughter-prefixes",
        required=True,
        help="Comma-separated module prefixes for daughter",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't store, just print what would be stored",
    )
    parser.add_argument(
        "--output",
        help="Also write JSON to this file",
    )

    args = parser.parse_args()

    daughter_prefixes = [p.strip() for p in args.daughter_prefixes.split(",")]

    result = build_and_store_graph(
        pydeps_json_path=args.pydeps,
        cluster_id=args.cluster,
        daughter_repo_id=args.daughter_repo,
        daughter_prefixes=daughter_prefixes,
        dry_run=args.dry_run,
    )

    if args.output and result.document:
        with open(args.output, "w") as f:
            json.dump(result.document, f, indent=2)
        print(f"Wrote JSON to {args.output}")

    print(result.message)
    print(f"  Nodes: {result.nodes}")
    print(f"  Edges: {result.edges}")
    print(f"  Cross-repo edges: {result.cross_repo_edges}")

    sys.exit(0 if result.success else 1)


if __name__ == "__main__":
    main()
