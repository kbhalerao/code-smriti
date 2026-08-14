"""
Symbol-aware module context builder.

The original module prompt was built from file *prose summaries* only
(`[f.content for f in file_indices]`), which discarded every concrete anchor the
file pass had already extracted — symbol names, real docstrings, imports. The
resulting module summaries named nothing and were strictly lossier than their own
inputs.

This module rebuilds that context from `file_index.metadata`, which is where the
detail actually lives.

Two properties worth knowing about the symbol lists it consumes:

1. Symbols arrive from two passes. Tree-sitter emits structural entries
   (`function`/`class`/`method`); the LLM chunker emits semantic ones
   (`schema`/`calculation`/`transform`/`json_schema`). Both passes emit an entry
   for the same construct without reconciling, so a class commonly appears twice
   under different types with disagreeing line ranges.
2. Only the structural line numbers are trustworthy. Verified against
   agkit.io-backend `tier1apps/gislayers/serializers.py`, LLM-emitted ranges are
   systematically 5-8 lines early — `multipolygon_to_geometry_collection_storage`
   claims L157-164 for code that actually lives at L165-178, no overlap at all.

So `merge_symbols` folds the two passes together keyed on name, always keeping
the structural line range, and emits LLM-only chunks with **no** line range
rather than a wrong one. Line numbers here should be resolved structurally or
not at all.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .schemas import FileIndex, ModuleSummary, SymbolRef

# Symbol types produced by the tree-sitter pass. Their line ranges come from the
# parser and are exact; everything else is LLM-derived and is not.
STRUCTURAL_TYPES = frozenset({"function", "class", "method"})

# Total characters of module context handed to the LLM. The previous pipeline
# capped this at 6000 chars after already truncating to the first 15 files; both
# limits predate the 262K-context models this now runs against.
DEFAULT_CHAR_BUDGET = 24000

# Per-symbol docstring allowance. Enough for the first couple of sentences,
# which is where the domain constraint usually is.
DOCSTRING_LIMIT = 240


@dataclass
class MergedSymbol:
    """A symbol after the structural and semantic passes have been reconciled."""
    name: str
    symbol_type: str
    docstring: Optional[str] = None
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    significant: bool = False

    @property
    def has_lines(self) -> bool:
        return self.start_line is not None and self.end_line is not None


@dataclass
class FileBlock:
    """Renderable context for one file, trimmable symbol-by-symbol under budget."""
    file_path: str
    language: str
    line_count: int
    summary: str
    imports: List[str] = field(default_factory=list)
    symbols: List[MergedSymbol] = field(default_factory=list)

    def render(self) -> str:
        head = f"### {self.file_path}"
        detail = ", ".join(
            p for p in (self.language, f"{self.line_count} lines" if self.line_count else "")
            if p
        )
        if detail:
            head += f" ({detail})"

        parts = [head]
        if self.summary:
            parts.append(self.summary)
        if self.imports:
            parts.append(f"Imports: {', '.join(self.imports)}")
        if self.symbols:
            parts.append("Symbols:")
            parts.extend(f"- {_render_symbol(s)}" for s in self.symbols)
        return "\n".join(parts)

    def size(self) -> int:
        return len(self.render())


def _render_symbol(sym: MergedSymbol) -> str:
    loc = f", L{sym.start_line}-{sym.end_line}" if sym.has_lines else ""
    line = f"{sym.name} ({sym.symbol_type}{loc})"
    if sym.docstring:
        line += f": {sym.docstring}"
    return line


def clean_docstring(raw: Optional[str]) -> str:
    """Strip quote delimiters and collapse whitespace, trimmed at a word boundary."""
    if not raw:
        return ""
    text = raw.strip()
    for delim in ('"""', "'''"):
        if text.startswith(delim):
            text = text[len(delim):]
        if text.endswith(delim):
            text = text[: -len(delim)]
    text = " ".join(text.split())
    if len(text) <= DOCSTRING_LIMIT:
        return text
    return text[:DOCSTRING_LIMIT].rsplit(" ", 1)[0] + "…"


def merge_symbols(symbols: List[SymbolRef]) -> List[MergedSymbol]:
    """
    Reconcile the structural and semantic symbol passes into one list.

    Keyed on name. The structural entry supplies the line range (the LLM's are
    unreliable, see module docstring); either pass may supply the docstring, with
    the structural one preferred since it is the literal source docstring rather
    than a description of it. LLM-only chunks survive as their own entries but
    carry no line range.
    """
    merged: Dict[str, MergedSymbol] = {}

    for sym in symbols:
        is_structural = sym.symbol_type in STRUCTURAL_TYPES
        doc = clean_docstring(sym.docstring)
        existing = merged.get(sym.name)

        if existing is None:
            merged[sym.name] = MergedSymbol(
                name=sym.name,
                symbol_type=sym.symbol_type,
                docstring=doc or None,
                start_line=sym.start_line if is_structural else None,
                end_line=sym.end_line if is_structural else None,
                significant=sym.is_significant,
            )
            continue

        # Structural pass wins on type and line range; a docstring fills a gap.
        if is_structural:
            existing.symbol_type = sym.symbol_type
            existing.start_line = sym.start_line
            existing.end_line = sym.end_line
            if doc:
                existing.docstring = doc
        elif not existing.docstring and doc:
            existing.docstring = doc

        existing.significant = existing.significant or sym.is_significant

    return list(merged.values())


def build_file_block(file_index: FileIndex, max_imports: int = 8) -> FileBlock:
    """Build the renderable context for one file, significant symbols only."""
    symbols = [s for s in merge_symbols(file_index.symbols) if s.significant]

    # Symbols carrying a docstring say the most per character, so they survive
    # budget trimming longest; within each group keep source order.
    symbols.sort(key=lambda s: (s.docstring is None, s.start_line or 1_000_000))

    return FileBlock(
        file_path=file_index.file_path,
        language=file_index.language,
        line_count=file_index.line_count,
        summary=(file_index.content or "").strip(),
        imports=list(file_index.imports[:max_imports]),
        symbols=symbols,
    )


def _fit_to_budget(blocks: List[FileBlock], budget: int, separator_cost: int) -> None:
    """
    Trim blocks in place until they fit, dropping from the largest block first.

    The previous pipeline cut the *tail* of the file list, so files late in the
    module vanished entirely. Trimming the largest block instead keeps every file
    represented and degrades detail evenly.
    """
    def total() -> int:
        return sum(b.size() for b in blocks) + separator_cost * max(0, len(blocks) - 1)

    while total() > budget:
        trimmable = [b for b in blocks if b.symbols]
        if not trimmable:
            break
        largest = max(trimmable, key=lambda b: b.size())
        largest.symbols.pop()


def build_module_context(
    file_indices: List[FileIndex],
    child_module_summaries: List[ModuleSummary],
    char_budget: int = DEFAULT_CHAR_BUDGET,
) -> str:
    """
    Build the symbol-aware context block for a module summary prompt.

    Every file gets a section; nested modules contribute their prose summary
    since their own symbol detail is already folded into them.
    """
    separator = "\n\n"
    blocks = [build_file_block(f) for f in file_indices if f.file_path]
    _fit_to_budget(blocks, char_budget, len(separator))

    sections = [b.render() for b in blocks]

    nested = [
        f"### {m.module_path}/ (submodule)\n{m.content.strip()}"
        for m in child_module_summaries
        if m.content and m.content.strip()
    ]

    return separator.join(sections + nested)
