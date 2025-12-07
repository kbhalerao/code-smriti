# Criticality Analysis

Computes PageRank-style criticality scores to identify which modules are most important across a codebase.

## Problem

In a family of related repos (e.g., `labcore` as a library consumed by `topsoil`, `farmworthdb`, etc.), changes to certain modules have outsized impact. A mixin like `AccessControlQuerySetMixin` might be used by 50+ models across 5 repos - but there's no way to know this without institutional knowledge.

We want to:
1. Identify which modules are most depended-upon (highest "blast radius")
2. Surface this during PR review ("this change affects 4 downstream repos")
3. Codify institutional knowledge about code importance

## Solution: PageRank on Import Graph

**Key insight**: PageRank was designed for exactly this - identifying important nodes in a directed graph based on who points to them.

```
Module A imports Module B
Module A imports Module C
Module D imports Module B
Module E imports Module B
```

In this graph, Module B has 3 incoming edges (A, D, E import it). PageRank will give B a higher score than C (only 1 importer).

### Why pydeps?

Import resolution is hard. Given `from clients.models import Client` in topsoil:
- Does topsoil have a `clients/` module? (No)
- Is `clients` from labcore? (Yes, via pip install)
- Which file defines `Client`?

**pydeps** solves this by running inside the project's virtualenv where Python's import system already knows the answers. It outputs JSON with resolved module paths.

```bash
# In project directory with virtualenv active:
pip install pydeps
pydeps tier1apps --show-deps --no-output > deps.json
```

Output format:
```json
{
  "tier1apps.foundations.models": {
    "name": "tier1apps.foundations.models",
    "path": "/path/to/tier1apps/foundations/models/__init__.py",
    "imports": ["django.db.models", "tier1apps.core.base"],
    "imported_by": ["tier1apps.clients.models", "tier1apps.contacts.models"],
    "bacon": 2
  }
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│              pydeps (run inside each project)                   │
│  Leverages Python's import system for resolution                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   pydeps_parser.py                              │
│  Parse JSON, filter to project modules, create DependencyEdge   │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   pagerank.py                                   │
│  Build NetworkX DiGraph → compute PageRank → CriticalityInfo    │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                   Output: JSON or Couchbase                     │
│  Per-module scores, category rankings, dependency metrics       │
└─────────────────────────────────────────────────────────────────┘
```

## Usage

### Analyze a single repo (intra-repo dependencies)

```bash
cd services/ingestion-worker

# Single package
uv run python -m v4.criticality.cli analyze \
  --repo kbhalerao/agkit.io-backend \
  --pydeps ../../t1deps.json \
  --prefixes tier1apps

# Multiple packages in same repo
uv run python -m v4.criticality.cli analyze \
  --repo kbhalerao/agkit.io-backend \
  --pydeps ../../t1deps.json,../../t2deps.json,../../t3deps.json \
  --prefixes tier1apps,tier2apps,tier3apps \
  --output criticality.json
```

### Generate pydeps output (run in target project)

```bash
# In the target project directory with virtualenv active:
pip install pydeps

# For a package named "myapp":
pydeps myapp --show-deps --no-output > myapp_deps.json

# Note: Run on package names, not directories
# pydeps . from root doesn't work - must specify package names
```

## Module Structure

| File | Purpose |
|------|---------|
| `schemas.py` | `DependencyEdge`, `CriticalityInfo`, `CriticalityScore` dataclasses |
| `registry.py` | Mother/daughter repo configuration |
| `pydeps_parser.py` | Parse pydeps JSON, detect cross-repo dependencies |
| `pagerank.py` | NetworkX graph building, PageRank computation |
| `cli.py` | Command-line interface |

## Data Model

### DependencyEdge

Represents a single import relationship:

```python
DependencyEdge(
    consumer_repo_id="kbhalerao/topsoil",
    consumer_module="topsoil.greet.views",
    provider_repo_id="kbhalerao/labcore",
    provider_module="clients.models",
    is_cross_repo=True,
)
```

### CriticalityInfo

Computed metrics for a module:

```python
CriticalityInfo(
    score=0.0694,              # Raw PageRank score
    normalized_score=1.0,       # Score / max_score
    percentile=99,              # 0-100, higher = more critical
    direct_dependents=92,       # Modules that import this
    in_degree=92,               # Same as direct_dependents
    out_degree=0,               # Modules this imports
)
```

## Example Output

```
======================================================================
TOP 10 MOST CRITICAL MODULES (by PageRank)
======================================================================
Rank  Module                                        Score    In    Out
----------------------------------------------------------------------
1     tier1apps                                     0.0694   92    0
2     tier1apps.foundations                         0.0489   59    0
3     tier1apps.foundations.models                  0.0345   36    0
4     tier1apps.contacts                            0.0262   37    0
5     tier1apps.associates                          0.0237   38    0

======================================================================
CATEGORY CRITICALITY (sum of PageRank)
======================================================================
  tier1apps.foundations: 0.2153 (30 modules)
  tier1apps.clients: 0.0974 (21 modules)
  tier1apps.contacts: 0.0917 (13 modules)
```

## Cross-Repo Analysis (Future)

For mother-daughter relationships (labcore → topsoil):

1. Run pydeps on daughter with mother pip-installed
2. The `path` field reveals which modules come from mother (contains `site-packages/labcore`)
3. `pydeps_parser.parse_pydeps_cross_repo()` identifies cross-repo edges
4. PageRank computed across the combined graph

Registry configuration in `registry.py`:

```python
REPO_REGISTRY = {
    "kbhalerao/labcore": MotherRepo(
        repo_id="kbhalerao/labcore",
        pip_package="labcore",
        path_markers=["/labcore/", "site-packages/labcore"],
        daughters=["kbhalerao/topsoil", "PeoplesCompany/farmworthdb", ...],
    ),
}
```

## Why PageRank Works

PageRank propagates "importance" through the graph:
- A module imported by many others gets a base score
- A module imported by *important* modules gets even higher score
- The damping factor (0.85) prevents score concentration

This captures both direct importance (many dependents) and transitive importance (depended on by important code).
