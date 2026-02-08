"""
Spec Document Detection and Metadata Extraction

Detects CodeAkriti spec documents by their structure and extracts
L-level metadata, intent patterns, and component references.

Spec docs are structured markdown describing the *why* of combining
UI components across six constraint levels (L0-L5).
"""

import re
from typing import Dict, List, Optional


# Pattern for L-level headers: "## ... (L5)", "## L3 — ...", "## L0: ...", etc.
_L_LEVEL_HEADER = re.compile(
    r"^##\s+.*?\b(L[0-5])\b",
    re.MULTILINE,
)

# Pattern for spec title: "# Spec: ..." or "# Feature Spec: ..."
_SPEC_TITLE = re.compile(
    r"^#\s+(?:Feature\s+)?Spec:\s*(.+)$",
    re.MULTILINE,
)

# Pattern for intent patterns blockquote: "> Intent Patterns: ..."
_INTENT_PATTERNS = re.compile(
    r"^>\s*Intent\s+Patterns?:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)

# Pattern for component names in L1 or BOM table rows: "| ComponentName |"
_BOM_TABLE_ROW = re.compile(
    r"^\|\s*`?([A-Z][A-Za-z0-9]+(?:[A-Z][a-z0-9]*)*)`?\s*\|",
    re.MULTILINE,
)

# Pattern for component references in L1 prose: backtick-wrapped PascalCase names
_COMPONENT_REF = re.compile(
    r"`([A-Z][A-Za-z0-9]+(?:[A-Z][a-z0-9]*)*)`",
)

# Pattern for component contract definitions in code blocks: "ComponentName:" at start of line
_CODE_BLOCK_COMPONENT = re.compile(
    r"^([A-Z][A-Za-z0-9]+(?:[A-Z][a-z0-9]*)*):$",
    re.MULTILINE,
)


_SKIP_NAMES = frozenset({
    "Component", "Name", "Props", "Description",
    "True", "False", "None",
    "State", "Heuristic", "Status", "Region",  # table column headers
})


def is_spec_document(content: str) -> bool:
    """Detect whether a markdown file is a spec document.

    A file is a spec if:
    - Title matches "# Spec: ..." or "# Feature Spec: ..."
    - OR contains 3+ L-level headers (L0-L5)
    """
    if _SPEC_TITLE.search(content):
        return True

    l_levels = _L_LEVEL_HEADER.findall(content)
    unique_levels = set(l_levels)
    return len(unique_levels) >= 3


def extract_spec_metadata(content: str) -> Dict:
    """Extract structured metadata from a spec document.

    Returns:
        dict with keys:
        - spec_name: str - from title line
        - intent_patterns: list[str] - from "> Intent Patterns:" blockquote
        - l_levels: list[str] - which L0-L5 sections are present
        - components: list[str] - component names from L1/BOM
    """
    # Spec name
    title_match = _SPEC_TITLE.search(content)
    spec_name = title_match.group(1).strip() if title_match else ""

    # Intent patterns
    intent_patterns: List[str] = []
    intent_match = _INTENT_PATTERNS.search(content)
    if intent_match:
        raw = intent_match.group(1)
        # Strip surrounding brackets (template format: [pattern1 | pattern2 | ...])
        raw = raw.strip().strip("[]")
        # Split on comma, pipe, or semicolon
        for pattern in re.split(r"[,|;]", raw):
            pattern = pattern.strip().strip("`").strip()
            # Skip empty, placeholders, and ellipsis
            if pattern and pattern not in ("...", "…"):
                intent_patterns.append(pattern)

    # L-levels present
    l_level_matches = _L_LEVEL_HEADER.findall(content)
    l_levels = sorted(set(l_level_matches), key=lambda x: int(x[1]))

    # Components from BOM table and L1 section
    components: List[str] = []
    seen: set = set()

    # BOM table rows
    for match in _BOM_TABLE_ROW.finditer(content):
        name = match.group(1)
        if name not in seen and name not in _SKIP_NAMES:
            seen.add(name)
            components.append(name)

    # Scan L1 section for component refs (backtick PascalCase + code-block definitions)
    l1_section = _extract_l1_section(content)
    scan_text = l1_section if l1_section else content

    # Code-block component definitions: "ComponentName:" at start of line
    for match in _CODE_BLOCK_COMPONENT.finditer(scan_text):
        name = match.group(1)
        if name not in seen and name not in _SKIP_NAMES:
            seen.add(name)
            components.append(name)

    # Backtick-wrapped PascalCase refs (likely component names)
    for match in _COMPONENT_REF.finditer(scan_text):
        name = match.group(1)
        if name not in seen and name not in _SKIP_NAMES and len(name) > 2:
            seen.add(name)
            components.append(name)

    return {
        "spec_name": spec_name,
        "intent_patterns": intent_patterns,
        "l_levels": l_levels,
        "components": components,
    }


def _extract_l1_section(content: str) -> Optional[str]:
    """Extract the L1 section content for component scanning."""
    # Find L1 header
    l1_match = re.search(r"^##\s+.*?\bL1\b.*$", content, re.MULTILINE)
    if not l1_match:
        return None

    start = l1_match.start()

    # Find next ## header after L1
    next_header = re.search(r"^##\s+", content[l1_match.end():], re.MULTILINE)
    if next_header:
        end = l1_match.end() + next_header.start()
    else:
        end = len(content)

    return content[start:end]
