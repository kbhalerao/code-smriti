"""
V4 Bottom-Up Aggregator

Aggregates summaries from bottom to top:
- Symbol summaries → File summary (done in FileProcessor)
- File summaries → Module summary
- Module summaries → Repo summary

The hierarchy follows the folder structure.
"""

from pathlib import Path
from collections import defaultdict
from typing import List, Dict, Optional, Set
from datetime import datetime

from loguru import logger

from .schemas import (
    FileIndex, ModuleSummary, RepoSummary, QualityInfo, VersionInfo,
    EnrichmentLevel, SCHEMA_VERSION,
    make_module_id, make_repo_id,
)
from .quality import QualityTracker
from .module_context import build_module_context


class BottomUpAggregator:
    """
    Aggregates file summaries into module and repo summaries.

    Uses LLM when available, with fallback to structural summaries.
    """

    def __init__(
        self,
        llm_enricher,
        quality_tracker: QualityTracker,
        enable_llm: bool = True,
    ):
        self.llm_enricher = llm_enricher
        self.quality_tracker = quality_tracker
        self.enable_llm = enable_llm

    def build_folder_tree(
        self,
        file_indices: List[FileIndex]
    ) -> Dict[str, List[FileIndex]]:
        """
        Group files by their parent folder.

        Returns:
            {folder_path: [files_in_folder]}
        """
        tree = defaultdict(list)

        for file_idx in file_indices:
            folder = str(Path(file_idx.file_path).parent)
            if folder == ".":
                folder = ""  # Root level
            tree[folder].append(file_idx)

        return dict(tree)

    def get_folder_hierarchy(
        self,
        folders: Set[str]
    ) -> List[str]:
        """
        Get folders in bottom-up order (deepest first).

        This ensures we process leaf folders before their parents.
        """
        # Sort by depth (descending), then alphabetically
        sorted_folders = sorted(
            folders,
            key=lambda f: (-f.count('/'), f)
        )
        return sorted_folders

    async def aggregate_module_summary(
        self,
        module_path: str,
        file_indices: List[FileIndex],
        child_module_summaries: List[ModuleSummary],
        repo_id: str,
        commit_hash: str,
        parent_module_id: str,
        use_llm: bool = True,
        existing_summary: Optional[tuple] = None,
    ) -> ModuleSummary:
        """
        Create a module_summary by aggregating file and nested module summaries.

        Args:
            module_path: Folder path relative to repo root
            file_indices: Files directly in this folder
            child_module_summaries: Nested module summaries (subfolders)
            repo_id: Repository identifier
            commit_hash: Git commit hash
            parent_module_id: document_id of parent (repo or parent module)
            use_llm: Whether to use LLM for this module (False = fallback only)
            existing_summary: Optional (content, EnrichmentLevel, summary_source)
                from the prior commit. Carried forward when this run won't
                LLM-regenerate the module, so an unaffected module keeps its LLM
                summary instead of being downgraded to the fallback. The source
                travels with the text so provenance stays truthful.

        Returns:
            ModuleSummary document
        """
        # Build the prompt context from file symbols rather than file prose. The
        # prose-only context this replaced discarded every symbol name, docstring
        # and import the file pass had already extracted, so module summaries
        # were strictly lossier than their own inputs and named nothing.
        module_context = build_module_context(file_indices, child_module_summaries)

        # Generate module summary. Preference order:
        #   1. Fresh LLM summary (module affected this run + LLM available)
        #   2. Prior LLM summary carried forward (unaffected module) — avoids the
        #      quality downgrade of overwriting a good summary with the fallback
        #   3. Mechanical structural fallback (no LLM summary ever / unavailable)
        summary, enrichment, summary_source = "", EnrichmentLevel.BASIC, ""

        if use_llm and self.enable_llm and self.quality_tracker.llm_available and module_context:
            summary, enrichment = await self._llm_module_summary(
                module_path, module_context, repo_id
            )
            if enrichment == EnrichmentLevel.LLM_SUMMARY:
                summary_source = "aggregated_from_symbols"

        if enrichment != EnrichmentLevel.LLM_SUMMARY and existing_summary:
            prev_content, prev_level, prev_source = existing_summary
            if prev_content and prev_level == EnrichmentLevel.LLM_SUMMARY:
                # Carry the prior provenance forward with the prior text. Stamping
                # a carried-over prose summary as symbol-aware would hide it from
                # every "which modules still need upgrading" query, permanently.
                # An unrecorded source is assumed not-yet-upgraded: a redundant
                # regeneration is cheap, a silently skipped one is not.
                summary, enrichment = prev_content, EnrichmentLevel.LLM_SUMMARY
                summary_source = prev_source or "unknown"

        if not summary:
            summary = self._fallback_module_summary(
                module_path, file_indices, child_module_summaries
            )
            enrichment = EnrichmentLevel.BASIC
            summary_source = "fallback"

        # Determine key files
        key_files = self._identify_key_files(file_indices)

        # Build children_ids
        children_ids = (
            [f.document_id for f in file_indices] +
            [m.document_id for m in child_module_summaries]
        )

        module_doc_id = make_module_id(repo_id, module_path)

        module_doc = ModuleSummary(
            document_id=module_doc_id,
            repo_id=repo_id,
            module_path=module_path,
            commit_hash=commit_hash,
            content=summary,
            file_count=len(file_indices),
            key_files=key_files,
            parent_id=parent_module_id,
            children_ids=children_ids,
            quality=QualityInfo(
                enrichment_level=enrichment,
                llm_available=self.quality_tracker.llm_available,
                summary_source=summary_source,
            ),
            version=VersionInfo(
                schema_version=SCHEMA_VERSION,
                pipeline_version=datetime.now().strftime("%Y.%m.%d"),
                created_at=datetime.now().isoformat(),
            ),
        )

        self.quality_tracker.record_module_created()
        return module_doc

    async def _llm_module_summary(
        self,
        module_path: str,
        module_context: str,
        repo_id: str
    ) -> tuple[str, EnrichmentLevel]:
        """Generate module summary using LLM."""
        try:
            result = await self.llm_enricher.enrich_module_symbol_aware(
                module_path=module_path,
                module_context=module_context,
                repo_id=repo_id
            )

            self.quality_tracker.record_llm_call(
                success=True,
                tokens=result.get("tokens", 0)
            )

            return result.get("summary", ""), EnrichmentLevel.LLM_SUMMARY

        except Exception as e:
            logger.debug(f"LLM module summary failed for {module_path}: {e}")
            self.quality_tracker.record_llm_call(success=False)
            return "", EnrichmentLevel.BASIC

    def _fallback_module_summary(
        self,
        module_path: str,
        file_indices: List[FileIndex],
        child_modules: List[ModuleSummary]
    ) -> str:
        """Generate fallback module summary from structure.

        Deduplicates identical file previews (common for generated/boilerplate
        files, e.g. many near-identical +server.js route handlers) and trims
        each preview at a sentence/word boundary rather than mid-word.
        """
        parts = [f"Module: {module_path or '(root)'}/"]

        if file_indices:
            parts.append(f"\n\nFiles ({len(file_indices)}):")
            seen_previews: set = set()
            shown = 0
            for f in file_indices:
                if shown >= 10:
                    break
                name = Path(f.file_path).name
                preview = self._preview(f.content) if f.content else ""
                key = preview.lower()
                if preview and key in seen_previews:
                    continue  # collapse duplicate summaries
                if preview:
                    seen_previews.add(key)
                parts.append(f"\n- {name}: {preview}" if preview else f"\n- {name}")
                shown += 1

        if child_modules:
            parts.append(f"\n\nSubmodules ({len(child_modules)}):")
            for m in child_modules[:5]:
                parts.append(f"\n- {m.module_path}/")

        return ''.join(parts)

    @staticmethod
    def _preview(content: str, limit: int = 160) -> str:
        """First sentence of a summary, trimmed at a word boundary (<= limit)."""
        text = content.split('\n', 1)[0].strip()
        idx = text.find('. ')
        if 0 < idx < limit:
            return text[:idx + 1]
        if len(text) <= limit:
            return text
        return text[:limit].rsplit(' ', 1)[0] + '…'

    # Recognisable entry points, most structurally revealing first. Order is
    # load-bearing: it ranks the result when more files qualify than fit.
    KEY_FILE_PATTERNS = [
        "models.py", "views.py", "urls.py",   # Django
        "index.ts", "index.js", "main.py",    # Entry points
        "api.py", "routes.py", "handlers.py",  # API
        "config.py", "settings.py",            # Config
        "__init__.py",                         # Python packages
    ]

    LARGE_FILE_LINES = 200

    def _identify_key_files(self, file_indices: List[FileIndex], limit: int = 10) -> List[str]:
        """
        Identify the most informative files in a module, as repo-relative paths.

        Returns paths rather than bare basenames. The old version stored
        `Path(f.file_path).name` with no deduplication, which — combined with the
        superseded-doc duplication that fed this method several copies of each
        file — produced entries like ["urls.py", "urls.py", "urls.py",
        "views.py", "urls.py"]: four indistinguishable names out of a 21-file
        count for a module that really has 7 files.

        Ranking, since more files can qualify than `limit` allows: recognised
        entry points first in KEY_FILE_PATTERNS order, then merely-large files
        by descending size. The previous `[:10]` kept whatever happened to come
        first in the input.
        """
        pattern_rank = {name: i for i, name in enumerate(self.KEY_FILE_PATTERNS)}

        matched: List[tuple] = []  # (sort key, file_path)
        seen: Set[str] = set()

        for f in file_indices:
            if not f.file_path or f.file_path in seen:
                continue
            seen.add(f.file_path)

            rank = pattern_rank.get(Path(f.file_path).name)
            if rank is not None:
                # Tier 0: named entry point, ordered by pattern priority.
                matched.append(((0, rank, f.file_path), f.file_path))
            elif f.line_count > self.LARGE_FILE_LINES:
                # Tier 1: large file, biggest first.
                matched.append(((1, -f.line_count, f.file_path), f.file_path))

        matched.sort(key=lambda pair: pair[0])
        return [path for _, path in matched[:limit]]

    async def aggregate_repo_summary(
        self,
        repo_id: str,
        commit_hash: str,
        module_summaries: List[ModuleSummary],
        file_indices: List[FileIndex]
    ) -> RepoSummary:
        """
        Create repo_summary by aggregating all module summaries.

        Args:
            repo_id: Repository identifier
            commit_hash: Git commit hash
            module_summaries: All module summaries
            file_indices: All file indices (for stats)

        Returns:
            RepoSummary document
        """
        # Collect module summaries
        module_contents = [m.content for m in module_summaries if m.content]

        # Generate repo summary
        if self.enable_llm and self.quality_tracker.llm_available and module_contents:
            summary, enrichment = await self._llm_repo_summary(
                repo_id, module_contents
            )
        else:
            summary = self._fallback_repo_summary(repo_id, module_summaries)
            enrichment = EnrichmentLevel.BASIC

        # Compute statistics
        total_files = len(file_indices)
        total_lines = sum(f.line_count for f in file_indices)

        # Language distribution
        languages = defaultdict(int)
        for f in file_indices:
            languages[f.language] += 1

        # Tech stack detection
        tech_stack = self._detect_tech_stack(file_indices)

        # Top-level modules
        top_modules = [
            m.module_path for m in module_summaries
            if '/' not in m.module_path and m.module_path  # No nested path
        ]

        repo_doc_id = make_repo_id(repo_id)

        repo_doc = RepoSummary(
            document_id=repo_doc_id,
            repo_id=repo_id,
            commit_hash=commit_hash,
            content=summary,
            total_files=total_files,
            total_lines=total_lines,
            languages=dict(languages),
            tech_stack=tech_stack,
            modules=top_modules,
            children_ids=[m.document_id for m in module_summaries if '/' not in m.module_path],
            quality=QualityInfo(
                enrichment_level=enrichment,
                llm_available=self.quality_tracker.llm_available,
                summary_source="aggregated_from_modules",
            ),
            version=VersionInfo(
                schema_version=SCHEMA_VERSION,
                pipeline_version=datetime.now().strftime("%Y.%m.%d"),
                created_at=datetime.now().isoformat(),
            ),
        )

        return repo_doc

    async def _llm_repo_summary(
        self,
        repo_id: str,
        module_summaries: List[str]
    ) -> tuple[str, EnrichmentLevel]:
        """Generate repo summary using LLM."""
        try:
            context = "\n\n---\n\n".join(module_summaries[:20])

            result = await self.llm_enricher.enrich_repo(
                repo_id=repo_id,
                modules_context=context
            )

            self.quality_tracker.record_llm_call(
                success=True,
                tokens=result.get("tokens", 0)
            )

            return result.get("summary", ""), EnrichmentLevel.LLM_SUMMARY

        except Exception as e:
            logger.debug(f"LLM repo summary failed for {repo_id}: {e}")
            self.quality_tracker.record_llm_call(success=False)
            return "", EnrichmentLevel.BASIC

    def _fallback_repo_summary(
        self,
        repo_id: str,
        module_summaries: List[ModuleSummary]
    ) -> str:
        """Generate fallback repo summary."""
        parts = [f"Repository: {repo_id}"]

        if module_summaries:
            # List top-level modules
            top_modules = [m for m in module_summaries if '/' not in m.module_path]
            if top_modules:
                parts.append(f"\n\nModules ({len(top_modules)}):")
                for m in top_modules[:10]:
                    parts.append(f"\n- {m.module_path}/: {m.file_count} files")

        return ''.join(parts)

    def _detect_tech_stack(self, file_indices: List[FileIndex]) -> List[str]:
        """Detect tech stack from file patterns and imports."""
        tech = set()

        for f in file_indices:
            path = f.file_path.lower()
            imports = f.imports

            # Framework detection
            if "django" in str(imports):
                tech.add("django")
            if "flask" in str(imports):
                tech.add("flask")
            if "fastapi" in str(imports):
                tech.add("fastapi")
            if "react" in str(imports) or "jsx" in path:
                tech.add("react")
            if "vue" in str(imports) or ".vue" in path:
                tech.add("vue")
            if "svelte" in path:
                tech.add("svelte")

            # Database detection
            if "sqlalchemy" in str(imports):
                tech.add("sqlalchemy")
            if "psycopg" in str(imports) or "postgresql" in str(imports):
                tech.add("postgresql")
            if "redis" in str(imports):
                tech.add("redis")
            if "celery" in str(imports):
                tech.add("celery")

            # File patterns
            if "requirements.txt" in path or "pyproject.toml" in path:
                tech.add("python")
            if "package.json" in path:
                tech.add("nodejs")
            if "dockerfile" in path:
                tech.add("docker")

        return sorted(tech)[:15]

    async def aggregate_all(
        self,
        file_indices: List[FileIndex],
        repo_id: str,
        commit_hash: str,
        affected_modules: set = None,
        existing_summaries: Dict[str, tuple] = None,
    ) -> tuple[List[ModuleSummary], RepoSummary]:
        """
        Build complete hierarchy from file indices.

        Args:
            file_indices: All file_index documents
            repo_id: Repository identifier
            commit_hash: Git commit hash
            affected_modules: If provided, only use LLM for these modules (optimization)
            existing_summaries: Optional
                {module_path: (content, EnrichmentLevel, summary_source)} from the
                prior commit. When a module is not being LLM-regenerated this run
                (unaffected), its prior LLM summary is carried forward instead of
                being downgraded to the mechanical fallback.

        Returns:
            (module_summaries, repo_summary)
        """
        existing_summaries = existing_summaries or {}
        # Group files by folder
        folder_tree = self.build_folder_tree(file_indices)
        all_folders = set(folder_tree.keys())

        # Also include parent folders that might not have direct files
        for folder in list(all_folders):
            parts = folder.split('/')
            for i in range(len(parts)):
                parent = '/'.join(parts[:i])
                if parent:
                    all_folders.add(parent)

        # Process folders bottom-up
        folder_order = self.get_folder_hierarchy(all_folders)

        module_summaries = {}  # {path: ModuleSummary}
        repo_doc_id = make_repo_id(repo_id)

        # Log optimization stats
        if affected_modules is not None:
            llm_count = len(affected_modules & all_folders)
            fallback_count = len(all_folders) - llm_count
            logger.info(f"  Module regeneration: {llm_count} LLM, {fallback_count} fallback (of {len(all_folders)} total)")

        for folder_path in folder_order:
            # Get direct files in this folder
            direct_files = folder_tree.get(folder_path, [])

            # Get child module summaries (immediate subfolders)
            child_modules = []
            for other_path, mod in module_summaries.items():
                # Check if other_path is immediate child of folder_path
                if folder_path:
                    if other_path.startswith(folder_path + '/'):
                        remaining = other_path[len(folder_path) + 1:]
                        if '/' not in remaining:  # Immediate child
                            child_modules.append(mod)
                else:
                    # Root: immediate children are top-level folders
                    if '/' not in other_path and other_path:
                        child_modules.append(mod)

            # Determine parent
            if folder_path:
                parent_folder = str(Path(folder_path).parent)
                if parent_folder == ".":
                    parent_folder = ""
                if parent_folder:
                    parent_id = make_module_id(repo_id, parent_folder)
                else:
                    parent_id = repo_doc_id  # Parent is repo
            else:
                parent_id = repo_doc_id  # Root folder, parent is repo

            # Create module summary
            if direct_files or child_modules:
                # Optimization: only use LLM for affected modules
                use_llm = (affected_modules is None) or (folder_path in affected_modules)
                if affected_modules and not use_llm:
                    logger.debug(f"  Skipping LLM for unaffected module: {folder_path}")

                module_summary = await self.aggregate_module_summary(
                    module_path=folder_path or "(root)",
                    file_indices=direct_files,
                    child_module_summaries=child_modules,
                    repo_id=repo_id,
                    commit_hash=commit_hash,
                    parent_module_id=parent_id,
                    use_llm=use_llm,
                    existing_summary=existing_summaries.get(folder_path),
                )
                module_summaries[folder_path] = module_summary

                # Update parent_id for direct files
                for f in direct_files:
                    f.parent_id = module_summary.document_id

        # Create repo summary
        all_modules = list(module_summaries.values())
        repo_summary = await self.aggregate_repo_summary(
            repo_id=repo_id,
            commit_hash=commit_hash,
            module_summaries=all_modules,
            file_indices=file_indices
        )

        return all_modules, repo_summary
