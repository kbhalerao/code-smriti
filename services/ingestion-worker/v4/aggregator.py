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
        parent_module_id: str
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

        Returns:
            ModuleSummary document
        """
        # Collect summaries from files
        file_summaries = [f.content for f in file_indices if f.content]

        # Collect summaries from nested modules
        nested_summaries = [m.content for m in child_module_summaries if m.content]

        all_summaries = file_summaries + nested_summaries

        # Generate module summary
        if self.enable_llm and self.quality_tracker.llm_available and all_summaries:
            summary, enrichment = await self._llm_module_summary(
                module_path, all_summaries, repo_id
            )
        else:
            summary = self._fallback_module_summary(
                module_path, file_indices, child_module_summaries
            )
            enrichment = EnrichmentLevel.BASIC

        # Determine key files
        key_files = self._identify_key_files(file_indices)

        # Build children_ids
        children_ids = (
            [f.document_id for f in file_indices] +
            [m.document_id for m in child_module_summaries]
        )

        module_doc_id = make_module_id(repo_id, module_path, commit_hash)

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
                summary_source="aggregated_from_files",
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
        summaries: List[str],
        repo_id: str
    ) -> tuple[str, EnrichmentLevel]:
        """Generate module summary using LLM."""
        try:
            # Combine summaries as context
            context = "\n\n---\n\n".join(summaries[:15])  # Limit

            result = await self.llm_enricher.enrich_module(
                module_path=module_path,
                files_context=context,
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
        """Generate fallback module summary from structure."""
        parts = [f"Module: {module_path or '(root)'}/"]

        if file_indices:
            parts.append(f"\n\nFiles ({len(file_indices)}):")
            for f in file_indices[:10]:
                name = Path(f.file_path).name
                # Extract first line of summary if available
                preview = f.content.split('\n')[0][:60] if f.content else ""
                parts.append(f"\n- {name}: {preview}")

        if child_modules:
            parts.append(f"\n\nSubmodules ({len(child_modules)}):")
            for m in child_modules[:5]:
                parts.append(f"\n- {m.module_path}/")

        return ''.join(parts)

    def _identify_key_files(self, file_indices: List[FileIndex]) -> List[str]:
        """Identify important files in a module."""
        key_patterns = [
            "models.py", "views.py", "urls.py",  # Django
            "index.ts", "index.js", "main.py",   # Entry points
            "api.py", "routes.py", "handlers.py", # API
            "config.py", "settings.py",           # Config
            "__init__.py",                        # Python packages
        ]

        key_files = []
        for f in file_indices:
            name = Path(f.file_path).name
            if name in key_patterns:
                key_files.append(name)
            elif f.line_count > 200:  # Large files are often important
                key_files.append(name)

        return key_files[:10]

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

        repo_doc_id = make_repo_id(repo_id, commit_hash)

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
        commit_hash: str
    ) -> tuple[List[ModuleSummary], RepoSummary]:
        """
        Build complete hierarchy from file indices.

        Args:
            file_indices: All file_index documents
            repo_id: Repository identifier
            commit_hash: Git commit hash

        Returns:
            (module_summaries, repo_summary)
        """
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
        repo_doc_id = make_repo_id(repo_id, commit_hash)

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
                    parent_id = make_module_id(repo_id, parent_folder, commit_hash)
                else:
                    parent_id = repo_doc_id  # Parent is repo
            else:
                parent_id = repo_doc_id  # Root folder, parent is repo

            # Create module summary
            if direct_files or child_modules:
                module_summary = await self.aggregate_module_summary(
                    module_path=folder_path or "(root)",
                    file_indices=direct_files,
                    child_module_summaries=child_modules,
                    repo_id=repo_id,
                    commit_hash=commit_hash,
                    parent_module_id=parent_id
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
