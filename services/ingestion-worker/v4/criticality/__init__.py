"""
V4 Criticality Analysis Module

Computes PageRank-style criticality scores from dependency graphs.
Supports both intra-repo (within a single project) and cross-repo
(mother→daughter) dependency analysis.

Key components:
- schemas.py: DependencyEdge, CriticalityInfo dataclasses
- registry.py: Mother/daughter repo configuration
- pydeps_parser.py: Parse pydeps JSON output
- pagerank.py: NetworkX graph building and PageRank computation
- cli.py: CLI entry points
"""

from .schemas import DependencyEdge, CriticalityInfo, make_edge_id
from .pagerank import build_graph, compute_pagerank, analyze_graph

__all__ = [
    "DependencyEdge",
    "CriticalityInfo",
    "make_edge_id",
    "build_graph",
    "compute_pagerank",
    "analyze_graph",
]
