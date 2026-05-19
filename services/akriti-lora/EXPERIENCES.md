# Akriti LoRA - Experiences & Observations

## Run 1: Original Exploration Approach (v1)

Generated 84 QA pairs quickly, but:
- **Hallucinated file paths** - References to `agkit/models/plugin.py`, `plugins/soil_test_importer.py` that don't exist
- **Too fast** - Didn't actually ground each answer in the codebase
- **Plausible but invented details** - Sounded authoritative but wasn't verified

## Run 2: Interview-Based Approach (v2)

Redesigned with persona-based "interviews" where the model asks questions and gets grounded answers.

### What Worked
- **Reactive questioning** - The interviewer DID follow threads based on what it learned
- **Incremental saves** - QA pairs appended to file as created (crash-safe)
- **Verification pass** - Double-checks answers against fresh codebase queries

### What Didn't Work
- **Stuck in migrations rabbit hole** - First search returned migration files, then every follow-up was about migrations
- **Hedging language** - "suggests", "implies", "likely" - needs confidence pass
- **Low diversity** - All 28 pairs ended up about schema evolution instead of actual domain (clients, farms, workflows, GIS)

### Technical Issues
- `ask_codebase` RAG endpoint times out frequently (504 Gateway Timeout)
- Switched to `search_codebase` only (faster, more reliable)
- Question extraction was grabbing fragments - added validation for proper sentence starts

## Ideas for Next Iteration

### Problem: Search Results Dominate the Conversation
The interviewer asks about "AgKit capabilities" but gets migration files back. Then it asks about migrations, gets more migrations, loops forever.

Potential fixes:
1. **Filter out migrations** from search results (treat as noise)
2. **Seed with domain overview** - Give interviewer a brief intro so it knows what to ask about
3. **Better first question** - Start with something that pulls domain content
4. **Rotate search levels** - Force doc-level searches sometimes, not just file-level
5. **Topic tracking** - If 3 questions in a row are about the same thing, force a pivot

### Problem: Hedging Language
Model says "The migrations suggest..." instead of "AgKit tracks soil test dates..."

Fix: Post-processing pass that rewrites with confidence, or adjust grounding prompt.

### Problem: Low QA Yield
~1 QA pair per turn. Could be fine (quality over quantity) or could push for more.

## Open Questions

- Should we pre-populate the interviewer with a domain glossary? (Client, Farm, Field, Workflow, etc.)
- Should we use module-level summaries from smriti instead of raw search?
- Is the interview metaphor the right one, or should we try something else entirely?
