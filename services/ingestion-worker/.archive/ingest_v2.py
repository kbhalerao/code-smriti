#!/usr/bin/env python3
"""
CodeSmriti Ingestion Pipeline V2

Hierarchical, LLM-enriched code indexing with quality scoring.

Usage:
    python ingest_v2.py --repo kbhalerao/labcore
    python ingest_v2.py --repo kbhalerao/labcore --enrich
    python ingest_v2.py --repo kbhalerao/labcore --enrich --model qwen3:3b
    python ingest_v2.py --audit-first  # Run audit, then re-index low quality
"""

import asyncio
import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict

from loguru import logger

# Import existing components
from config import WorkerConfig
from parsers.code_parser import CodeParser, CodeChunk, should_skip_file
from parsers.document_parser import DocumentParser, DocumentChunk
from embeddings.local_generator import LocalEmbeddingGenerator
from storage.couchbase_client import CouchbaseClient
from llm_enricher import LLMEnricher, LLMConfig, LMSTUDIO_CONFIG, EnrichmentResult
from chunk_versioning import (
    SchemaVersion, EnrichmentLevel, CURRENT_SCHEMA_VERSION,
    CURRENT_PIPELINE_VERSION, create_version_metadata, estimate_enrichment_cost
)

config = WorkerConfig()


@dataclass
class HierarchyNode:
    """A node in the chunk hierarchy"""
    chunk_id: str
    type: str  # "repo", "module", "file", "symbol"
    path: str
    parent_id: Optional[str]
    children_ids: List[str] = field(default_factory=list)
    metadata: Dict = field(default_factory=dict)


@dataclass
class EnrichedChunk:
    """A chunk with V2/V3 metadata and versioning"""
    chunk_id: str
    type: str
    repo_id: str
    file_path: str
    content: str
    language: Optional[str]
    metadata: Dict
    embedding: Optional[List[float]]

    # V2 additions
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)
    summary: Optional[str] = None
    purpose: Optional[str] = None
    quality_score: float = 0.5
    llm_enriched: bool = False
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    # V3 versioning
    enrichment_level: str = "none"  # none, basic, llm_summary, llm_full
    enrichment_cost: int = 0  # estimated tokens used

    def to_dict(self) -> Dict:
        # Determine enrichment level for versioning
        if self.llm_enriched and self.purpose:
            enrich_level = EnrichmentLevel.LLM_FULL
        elif self.llm_enriched:
            enrich_level = EnrichmentLevel.LLM_SUMMARY
        elif self.metadata.get("symbols"):
            enrich_level = EnrichmentLevel.BASIC
        else:
            enrich_level = EnrichmentLevel.NONE

        # Create version metadata
        version = create_version_metadata(
            enrichment_level=enrich_level,
            enrichment_cost=self.enrichment_cost,
            protect=self.llm_enriched  # Auto-protect LLM-enriched chunks
        )

        return {
            "chunk_id": self.chunk_id,
            "type": self.type,
            "repo_id": self.repo_id,
            "file_path": self.file_path,
            "content": self.content,
            "language": self.language,
            "metadata": self.metadata,
            "embedding": self.embedding,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "summary": self.summary,
            "purpose": self.purpose,
            "quality_score": self.quality_score,
            "llm_enriched": self.llm_enriched,
            "created_at": self.created_at,
            "version": version  # V3 versioning metadata
        }


class IngestionPipelineV2:
    """
    V2 Ingestion Pipeline with hierarchical chunking and LLM enrichment
    """

    def __init__(
        self,
        enable_llm: bool = False,
        llm_config: LLMConfig = LMSTUDIO_CONFIG,  # MacStudio LM Studio
        importance_threshold: float = 0.4
    ):
        logger.info("Initializing V2 Ingestion Pipeline")

        # Core components
        self.code_parser = CodeParser()
        self.doc_parser = DocumentParser()
        self.embedding_generator = LocalEmbeddingGenerator()
        self.db = CouchbaseClient()

        # LLM enrichment (optional)
        self.enable_llm = enable_llm
        self.llm_config = llm_config
        self.llm_enricher = None
        self.importance_threshold = importance_threshold

        if enable_llm:
            self.llm_enricher = LLMEnricher(llm_config)
            logger.info(f"LLM enrichment enabled: {llm_config.model}")

        # Repository path
        repos_base = config.repos_path if hasattr(config, 'repos_path') else "/repos"
        self.repos_path = Path(repos_base).resolve()

        logger.info("V2 Pipeline initialized")

    def detect_modules(self, repo_path: Path) -> List[Dict]:
        """
        Detect modules/packages in a repository

        Looks for:
        - Django apps (has models.py, views.py, or apps.py)
        - Python packages (has __init__.py)
        - Major directories with code
        """
        modules = []

        for item in repo_path.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith('.') or item.name in ['node_modules', 'venv', '.venv', '__pycache__']:
                continue

            module_info = {
                "path": item.name,
                "type": "directory",
                "files": [],
                "is_django_app": False,
                "is_python_package": False
            }

            files_in_module = list(item.rglob("*"))
            code_files = [f for f in files_in_module if f.suffix in ['.py', '.js', '.ts', '.svelte']]
            module_info["files"] = [str(f.relative_to(repo_path)) for f in code_files[:50]]

            # Check for Django app
            if (item / "models.py").exists() or (item / "views.py").exists():
                module_info["is_django_app"] = True
                module_info["type"] = "django_app"

            # Check for Python package
            if (item / "__init__.py").exists():
                module_info["is_python_package"] = True
                if not module_info["is_django_app"]:
                    module_info["type"] = "python_package"

            if code_files:
                modules.append(module_info)

        return modules

    def calculate_file_importance(self, file_path: Path, repo_path: Path) -> float:
        """
        Calculate importance score for a file (0-1)
        Higher = more likely to enrich with LLM
        """
        score = 0.3  # Base score
        rel_path = str(file_path.relative_to(repo_path)).lower()
        file_name = file_path.name.lower()

        # Boost for key files
        if any(kw in file_name for kw in ['models', 'views', 'serializers', 'admin', 'forms']):
            score += 0.25
        if file_name in ['settings.py', 'urls.py', 'wsgi.py', 'asgi.py']:
            score += 0.2
        if file_name == '__init__.py':
            score -= 0.1
        if 'test' in rel_path:
            score -= 0.2
        if 'migrations' in rel_path:
            score -= 0.3

        # Boost for larger files (more likely to be important)
        try:
            size = file_path.stat().st_size
            if size > 5000:
                score += 0.1
            if size > 15000:
                score += 0.1
        except:
            pass

        return max(0.0, min(1.0, score))

    def calculate_quality_score(self, chunk: EnrichedChunk) -> float:
        """Calculate quality score for a chunk"""
        score = 0.5

        # Content quality
        if chunk.content and len(chunk.content) > 200:
            score += 0.1
        if chunk.content and len(chunk.content) < 50:
            score -= 0.2

        # Metadata completeness
        if chunk.metadata.get("commit_hash"):
            score += 0.05
        if chunk.metadata.get("docstring"):
            score += 0.1

        # LLM enrichment
        if chunk.llm_enriched:
            score += 0.15
        if chunk.summary:
            score += 0.1

        # Hierarchy
        if chunk.parent_id:
            score += 0.05

        return max(0.0, min(1.0, score))

    async def create_repo_summary(self, repo_id: str, repo_path: Path, modules: List[Dict]) -> EnrichedChunk:
        """Create a repository-level summary chunk"""

        # Gather basic info
        all_files = list(repo_path.rglob("*"))
        code_files = [f for f in all_files if f.suffix in ['.py', '.js', '.ts', '.svelte', '.html', '.css']]
        doc_files = [f for f in all_files if f.suffix in ['.md', '.rst', '.txt']]

        # Read README if exists
        readme_content = ""
        for readme_name in ['README.md', 'README.rst', 'README.txt', 'README']:
            readme_path = repo_path / readme_name
            if readme_path.exists():
                try:
                    readme_content = readme_path.read_text()[:3000]
                except:
                    pass
                break

        # Build content
        content = f"""# Repository: {repo_id}

## Overview
Total files: {len(all_files)}
Code files: {len(code_files)}
Documentation files: {len(doc_files)}

## Modules
{chr(10).join(f"- {m['path']} ({m['type']})" for m in modules[:15])}

## README
{readme_content if readme_content else "(No README found)"}
"""

        chunk_id = hashlib.sha256(f"repo_summary:{repo_id}".encode()).hexdigest()

        chunk = EnrichedChunk(
            chunk_id=chunk_id,
            type="repo_summary",
            repo_id=repo_id,
            file_path="",
            content=content,
            language=None,
            metadata={
                "total_files": len(all_files),
                "code_files": len(code_files),
                "doc_files": len(doc_files),
                "modules": [m["path"] for m in modules]
            },
            embedding=None
        )

        # LLM enrichment for repo summary
        if self.enable_llm and self.llm_enricher:
            try:
                # Get sample files for context
                sample_files = {}
                key_files = ['README.md', 'models.py', 'views.py', 'settings.py']
                for kf in key_files:
                    found = list(repo_path.rglob(kf))[:1]
                    if found:
                        try:
                            sample_files[str(found[0].relative_to(repo_path))] = found[0].read_text()[:2000]
                        except:
                            pass

                enriched_summary = await self.llm_enricher.generate_repo_summary(
                    repo_id,
                    [str(f.relative_to(repo_path)) for f in code_files[:100]],
                    sample_files
                )
                chunk.content = enriched_summary
                chunk.llm_enriched = True
                logger.info(f"LLM enriched repo summary for {repo_id}")
            except Exception as e:
                logger.warning(f"LLM enrichment failed for repo summary: {e}")

        chunk.quality_score = self.calculate_quality_score(chunk)
        return chunk

    async def create_module_summary(
        self,
        repo_id: str,
        module: Dict,
        repo_path: Path,
        parent_chunk_id: str
    ) -> EnrichedChunk:
        """Create a module-level summary chunk"""

        module_path = repo_path / module["path"]

        # Read key files
        key_file_contents = {}
        for kf in ['models.py', 'views.py', 'serializers.py', 'admin.py', '__init__.py']:
            kf_path = module_path / kf
            if kf_path.exists():
                try:
                    key_file_contents[kf] = kf_path.read_text()[:2000]
                except:
                    pass

        # Build content
        content = f"""# Module: {module['path']}

Type: {module['type']}
Files: {len(module['files'])}

## Key Files
{chr(10).join(f"- {f}" for f in module['files'][:20])}
"""

        chunk_id = hashlib.sha256(f"module:{repo_id}:{module['path']}".encode()).hexdigest()

        chunk = EnrichedChunk(
            chunk_id=chunk_id,
            type="module_summary",
            repo_id=repo_id,
            file_path=module["path"],
            content=content,
            language="python" if module.get("is_python_package") else None,
            metadata={
                "module_type": module["type"],
                "file_count": len(module["files"]),
                "is_django_app": module.get("is_django_app", False)
            },
            embedding=None,
            parent_id=parent_chunk_id
        )

        # LLM enrichment
        if self.enable_llm and self.llm_enricher and key_file_contents:
            try:
                enriched = await self.llm_enricher.generate_module_summary(
                    module["path"],
                    module["files"],
                    key_file_contents
                )
                chunk.content = enriched
                chunk.llm_enriched = True
                logger.info(f"LLM enriched module summary for {module['path']}")
            except Exception as e:
                logger.warning(f"LLM enrichment failed for module {module['path']}: {e}")

        chunk.quality_score = self.calculate_quality_score(chunk)
        return chunk

    async def process_file_v2(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str,
        parent_module_id: Optional[str]
    ) -> List[EnrichedChunk]:
        """
        Process a single file with V2 chunking

        Returns list of chunks (file chunk + symbol chunks if needed)
        """
        chunks = []

        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"Could not read {file_path}: {e}")
            return []

        if len(content.strip()) < 50:
            return []

        relative_path = str(file_path.relative_to(repo_path))
        language = self.code_parser.detect_language(file_path)

        # Get git metadata
        git_metadata = self.code_parser.get_git_metadata(repo_path, relative_path)

        # Calculate importance for LLM enrichment
        importance = self.calculate_file_importance(file_path, repo_path)

        # Create file chunk
        file_chunk_id = hashlib.sha256(
            f"file:{repo_id}:{relative_path}:{git_metadata.get('commit_hash', '')}".encode()
        ).hexdigest()

        file_chunk = EnrichedChunk(
            chunk_id=file_chunk_id,
            type="file",
            repo_id=repo_id,
            file_path=relative_path,
            content=content,
            language=language,
            metadata={
                **git_metadata,
                "line_count": content.count('\n'),
                "file_size": len(content),
                "importance_score": importance
            },
            embedding=None,
            parent_id=parent_module_id
        )

        # LLM enrichment for important files
        if (self.enable_llm and
            self.llm_enricher and
            importance >= self.importance_threshold and
            language):
            try:
                enrichment = await self.llm_enricher.enrich_file(
                    relative_path, content, language
                )
                file_chunk.summary = enrichment.summary
                file_chunk.purpose = enrichment.purpose
                file_chunk.metadata["key_symbols"] = enrichment.key_symbols
                file_chunk.metadata["integrations"] = enrichment.integrations
                file_chunk.llm_enriched = True
                logger.debug(f"LLM enriched: {relative_path}")
            except Exception as e:
                logger.warning(f"LLM enrichment failed for {relative_path}: {e}")

        file_chunk.quality_score = self.calculate_quality_score(file_chunk)
        chunks.append(file_chunk)

        # For large files, also create symbol chunks
        if len(content) > 6000 and language in ("python", "sql"):
            try:
                # Dispatch to appropriate parser
                if language == "python":
                    symbol_chunks = await self.code_parser.parse_python_file(
                        file_path, content, repo_id, relative_path, git_metadata
                    )
                elif language == "sql":
                    symbol_chunks = await self.code_parser.parse_sql_file(
                        file_path, content, repo_id, relative_path, git_metadata
                    )
                else:
                    symbol_chunks = []

                for sc in symbol_chunks:
                    enriched = EnrichedChunk(
                        chunk_id=sc.chunk_id,
                        type=f"symbol_{sc.chunk_type}",
                        repo_id=repo_id,
                        file_path=relative_path,
                        content=sc.code_text,
                        language=language,
                        metadata=sc.metadata,
                        embedding=None,
                        parent_id=file_chunk_id
                    )
                    enriched.quality_score = self.calculate_quality_score(enriched)
                    chunks.append(enriched)
                    file_chunk.children_ids.append(sc.chunk_id)
            except Exception as e:
                logger.warning(f"Symbol extraction failed for {relative_path}: {e}")

        return chunks

    async def generate_embeddings(self, chunks: List[EnrichedChunk]) -> List[EnrichedChunk]:
        """Generate embeddings for all chunks"""
        if not chunks:
            return chunks

        texts = []
        for chunk in chunks:
            # Build embedding text
            text = chunk.content
            if chunk.summary:
                text = f"{chunk.summary}\n\n{text}"
            texts.append(text[:8000])  # Truncate for embedding model

        # Generate embeddings in batch
        embeddings = self.embedding_generator.model.encode(
            texts,
            batch_size=config.embedding_batch_size
        )

        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb.tolist()

        return chunks

    async def process_repository(self, repo_id: str):
        """
        Process a repository with V2 pipeline
        """
        logger.info(f"=== V2 Processing: {repo_id} ===")
        start_time = datetime.now()

        # Get repo path
        repo_path = self.repos_path / repo_id.replace("/", "_")
        if not repo_path.exists():
            logger.error(f"Repository not found: {repo_path}")
            return

        all_chunks = []

        # 1. Detect modules
        modules = self.detect_modules(repo_path)
        logger.info(f"Found {len(modules)} modules")

        # 2. Create repo summary
        repo_summary = await self.create_repo_summary(repo_id, repo_path, modules)
        all_chunks.append(repo_summary)

        # 3. Create module summaries
        module_id_map = {}
        for module in modules:
            module_chunk = await self.create_module_summary(
                repo_id, module, repo_path, repo_summary.chunk_id
            )
            all_chunks.append(module_chunk)
            module_id_map[module["path"]] = module_chunk.chunk_id
            repo_summary.children_ids.append(module_chunk.chunk_id)

        # 4. Process all files
        logger.info("Processing files...")
        code_files = []
        for ext in config.supported_code_extensions:
            code_files.extend(repo_path.rglob(f"*{ext}"))

        processed = 0
        skipped = 0
        for file_path in code_files:
            if should_skip_file(file_path):
                skipped += 1
                continue

            # Find parent module
            rel_parts = file_path.relative_to(repo_path).parts
            parent_module_id = None
            if rel_parts:
                parent_module_id = module_id_map.get(rel_parts[0], repo_summary.chunk_id)

            file_chunks = await self.process_file_v2(
                file_path, repo_path, repo_id, parent_module_id
            )
            all_chunks.extend(file_chunks)
            processed += 1

            if processed % 50 == 0:
                logger.info(f"Processed {processed} files...")

        logger.info(f"Files: {processed} processed, {skipped} skipped")
        logger.info(f"Total chunks: {len(all_chunks)}")

        # 5. Generate embeddings
        logger.info("Generating embeddings...")
        all_chunks = await self.generate_embeddings(all_chunks)

        # 6. Store chunks
        logger.info("Storing chunks...")
        success = 0
        failed = 0
        for chunk in all_chunks:
            try:
                self.db.collection.upsert(chunk.chunk_id, chunk.to_dict())
                success += 1
            except Exception as e:
                logger.error(f"Failed to store chunk {chunk.chunk_id}: {e}")
                failed += 1

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"=== Completed {repo_id} in {elapsed:.1f}s ===")
        logger.info(f"Stored: {success}, Failed: {failed}")

        # Quality summary
        quality_dist = defaultdict(int)
        for chunk in all_chunks:
            if chunk.quality_score >= 0.8:
                quality_dist["high"] += 1
            elif chunk.quality_score >= 0.6:
                quality_dist["medium"] += 1
            elif chunk.quality_score >= 0.4:
                quality_dist["low"] += 1
            else:
                quality_dist["critical"] += 1

        logger.info(f"Quality: {dict(quality_dist)}")
        llm_enriched = sum(1 for c in all_chunks if c.llm_enriched)
        logger.info(f"LLM enriched: {llm_enriched}/{len(all_chunks)}")


async def main():
    parser = argparse.ArgumentParser(description="CodeSmriti V2 Ingestion")
    parser.add_argument("--repo", type=str, required=True, help="Repository ID (owner/repo)")
    parser.add_argument("--enrich", action="store_true", help="Enable LLM enrichment")
    parser.add_argument("--model", type=str, default="qwen3-3b", help="LLM model name")
    parser.add_argument("--llm-host", type=str, default="macstudio.local", help="LLM host")
    parser.add_argument("--llm-port", type=int, default=1234, help="LLM port")
    parser.add_argument("--threshold", type=float, default=0.4, help="Importance threshold for LLM")
    args = parser.parse_args()

    llm_config = LLMConfig(
        provider="lmstudio",
        model=args.model,
        base_url=f"http://{args.llm_host}:{args.llm_port}",
        temperature=0.3
    )

    pipeline = IngestionPipelineV2(
        enable_llm=args.enrich,
        llm_config=llm_config,
        importance_threshold=args.threshold
    )

    await pipeline.process_repository(args.repo)


if __name__ == "__main__":
    asyncio.run(main())
