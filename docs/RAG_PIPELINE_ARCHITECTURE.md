# RAG Pipeline Architecture

## Overview

code-smriti provides a two-tier intelligence system for querying indexed codebases:

1. **Local LLM tier** - Fast, private, constrained queries handled by Qwen3
2. **SOTA tier** - Complex reasoning handled by Claude (via Claude Code)

The MCP (Model Context Protocol) server enables Claude Code to access the indexed knowledge base, while the unified RAG pipeline provides intelligent query handling for both tiers.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Claude Code (SOTA)                            │
│    Complex reasoning, architecture decisions, cross-cutting analysis    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ MCP Protocol
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         MCP Server (Client-side)                        │
│                    services/mcp-server/rag_mcp_server.py                │
│                                                                         │
│  Tools:                                                                 │
│  • list_repos        - Discover indexed repositories                    │
│  • explore_structure - Navigate directory structure                     │
│  • search_codebase   - Semantic search at any level                     │
│  • get_file          - Retrieve source code                             │
│  • ask_codebase      - Developer questions (→ /api/rag/ask/code)        │
│  • ask_agsci         - Sales/proposal questions (→ /api/rag/ask/proposal)│
│  • affected_tests    - Dependency-based test selection                  │
│  • get_criticality   - Module importance analysis                       │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTTPS (smriti.agsci.com)
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         API Server (FastAPI)                            │
│                    services/api-server/app/                             │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    Unified RAG Pipeline (Local LLM)                     │
│                      app/rag/pipeline.py                                │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 1. Intent Classification (Qwen3 tool call, ~2-3s)               │   │
│  │    • Classifies query type (code_explanation, architecture,     │   │
│  │      impact_analysis, capability_check, proposal_draft, etc.)   │   │
│  │    • Determines search direction (broad/narrow/specific)        │   │
│  │    • Extracts entities and search keywords (query expansion)    │   │
│  │    • Uses conversation history for context                      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 2. Retrieval with Progressive Drilldown (~2-5s)                 │   │
│  │    • Starts at intent-appropriate level (repo/module/file/symbol)│   │
│  │    • If results inadequate, tries adjacent levels               │   │
│  │    • Fetches parent context for grounding                       │   │
│  │    • Uses expanded query for better semantic matching           │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                              │                                          │
│                              ▼                                          │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ 3. Synthesis (Qwen3, ~5-15s)                                    │   │
│  │    • Intent-specific prompt templates                           │   │
│  │    • Constrained output with citations                          │   │
│  │    • Gap identification ([GAP: ...] markers)                    │   │
│  │    • Conversation-aware context                                 │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                      Couchbase (Vector Store)                           │
│                                                                         │
│  Document Types (V4 Hierarchy):                                         │
│  • repo_summary    - One per repository, high-level overview            │
│  • module_summary  - One per directory, folder-level context            │
│  • file_index      - One per file, file-level summary                   │
│  • symbol_index    - Functions/classes ≥5 lines                         │
│  • document        - Documentation files (RST, MD)                      │
│  • repo_bdr        - Business development briefs                        │
│                                                                         │
│  + Dependency Graph (for impact analysis)                               │
└─────────────────────────────────────────────────────────────────────────┘
```

## Personas and Intents

The pipeline supports two personas with different intent sets:

### Developer Persona

| Intent | Description | Primary Doc Types | Example Query |
|--------|-------------|-------------------|---------------|
| `code_explanation` | How does X work? | file_index, symbol_index | "How does authentication work?" |
| `architecture` | How is X organized? | module_summary | "What's the structure of the API?" |
| `impact_analysis` | What depends on X? | file_index + graph | "What breaks if I change UserModel?" |
| `specific_lookup` | Find exact entity | symbol_index | "Find the authenticate function" |
| `documentation` | Guidelines/principles | document | "What are the coding standards?" |

### Sales Persona

| Intent | Description | Primary Doc Types | Example Query |
|--------|-------------|-------------------|---------------|
| `capability_check` | Can we do X? | repo_bdr, repo_summary | "Can we build a GIS platform?" |
| `proposal_draft` | Write proposal section | module_summary, repo_bdr | "Draft technical approach for mobile app" |
| `experience_summary` | Relevant past work | repo_summary, repo_bdr | "What's our experience with ag-tech?" |

## Progressive Drilldown Strategy

Based on intent classification, the pipeline selects a search strategy:

```
BROAD direction:    repo → module → file
NARROW direction:   file → symbol → module
SPECIFIC direction: symbol → file
```

At each level, results are scored. If inadequate (< 2 results with score ≥ 0.65), the pipeline continues to the next level.

## Query Expansion

Intent classification includes query expansion via Qwen3 tool calling:

```json
{
  "intent": "code_explanation",
  "direction": "narrow",
  "entities": ["authentication", "OAuth"],
  "search_keywords": [
    "authentication", "auth", "login", "session",
    "OAuth", "token", "JWT", "bearer", "middleware"
  ],
  "repo_scope": null
}
```

The expanded keywords are concatenated with the original query before embedding, improving semantic search recall.

## API Endpoints

### Unified Pipeline

```
POST /api/rag/ask
{
  "query": "How does authentication work?",
  "persona": "developer",  // or "sales"
  "conversation_history": [...]
}

Response:
{
  "answer": "...",
  "intent": "code_explanation",
  "direction": "narrow",
  "sources": ["repo/file.py", ...],
  "levels_searched": ["file", "symbol"],
  "adequate_context": true,
  "gaps": []
}
```

### Convenience Endpoints

```
POST /api/rag/ask/code      # Forces developer persona
POST /api/rag/ask/proposal  # Forces sales persona
```

## MCP Server Configuration

The MCP server runs locally (client-side) and calls the API server. Configure in `.mcp.json`:

```json
{
  "mcpServers": {
    "code-smriti": {
      "command": "python",
      "args": ["-m", "services.mcp-server.rag_mcp_server"],
      "cwd": "/path/to/code-smriti",
      "env": {
        "CODESMRITI_API_URL": "https://smriti.agsci.com",
        "CODESMRITI_USERNAME": "your-email",
        "CODESMRITI_PASSWORD": "your-password"
      }
    }
  }
}
```

## Two-Tier Intelligence

### When Local LLM Handles

- Routine code questions
- Specific lookups
- Proposal section drafting
- Capability checks with clear evidence

### When Claude Code Drives

- Complex architectural analysis
- Cross-cutting concerns
- Multi-step debugging
- Decisions requiring judgment

Claude Code determines this based on MCP tool descriptions. There's no explicit "escalation" - Claude uses its judgment about when to delegate to local tools vs. reason directly.

## File Structure

```
services/api-server/app/rag/
├── intent.py           # IntentClassifier (Qwen3 tool call)
├── orchestrator.py     # RetrievalOrchestrator (progressive drilldown)
├── synthesis.py        # Synthesizer (intent-specific prompts)
├── pipeline.py         # RAGPipeline (ties it together)
├── models.py           # Pydantic models
├── tools.py            # Low-level search/retrieval
├── graph_tools.py      # Dependency graph tools
└── prompts/            # Intent-specific prompt templates
    ├── code_explanation.md
    ├── architecture.md
    ├── impact_analysis.md
    ├── capability_check.md
    ├── proposal_draft.md
    ├── experience_summary.md
    ├── specific_lookup.md
    └── documentation.md

services/mcp-server/
└── rag_mcp_server.py   # MCP server (client-side, calls API)
```

## Performance Characteristics

| Stage | Typical Latency |
|-------|-----------------|
| Intent Classification | 2-3s |
| Retrieval (per level) | 1-2s |
| Parent Context Fetch | 0.5-1s |
| Synthesis | 5-15s |
| **Total** | **10-20s** |

This is acceptable for thoughtful answers. For faster responses, use `search_codebase` directly (Claude does its own synthesis).

## Customization

### Adding New Intents

1. Add to `QueryIntent` enum in `intent.py`
2. Add to appropriate `PERSONA_INTENTS` set
3. Add start level in `INTENT_START_LEVELS` (orchestrator.py)
4. Create prompt template in `prompts/`

### Customizing Prompts

Prompt templates are loaded from `app/rag/prompts/`. Edit the `.md` files to customize output format. Templates use `{context}`, `{parent_context}`, and `{query}` placeholders.

### Adjusting Drilldown

Modify `DRILLDOWN_PATHS` in `orchestrator.py` to change the search strategy per direction. Adjust `score_threshold` and `min_good_results` to tune adequacy detection.
