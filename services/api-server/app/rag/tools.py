"""
RAG Tool Implementations

Core tool logic shared between MCP and LLM modes.
"""

import os
import fnmatch
from pathlib import Path
from typing import List, Optional

from loguru import logger
from sentence_transformers import SentenceTransformer
from couchbase.options import QueryOptions

from app.database.couchbase_client import CouchbaseClient
from app.rag.models import (
    RepoInfo,
    FileInfo,
    StructureInfo,
    SearchResult,
    FileContent,
    SearchLevel,
    LEVEL_TO_DOCTYPE,
)


# Language detection by extension
EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".jsx": "javascript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".md": "markdown",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".r": "r",
    ".R": "r",
}

# Key file patterns for auto-detection
KEY_FILE_PATTERNS = {
    "config": ["pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle"],
    "readme": ["README.md", "README.rst", "README.txt", "README"],
    "entry": ["main.py", "app.py", "index.js", "index.ts", "main.go", "main.rs", "App.java"],
    "dockerfile": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
    "ci": [".github/workflows/*.yml", ".gitlab-ci.yml", "Jenkinsfile"],
}


async def list_repos(
    db: CouchbaseClient,
    tenant_id: str = "code_kosha"
) -> List[RepoInfo]:
    """
    List all indexed repositories.

    Args:
        db: Couchbase client
        tenant_id: Tenant bucket name

    Returns:
        List of RepoInfo sorted by document count
    """
    try:
        # Query V4 document types
        # Note: 'language' is a reserved word in N1QL, so we use backticks to escape it
        n1ql = f"""
            SELECT repo_id, COUNT(*) as doc_count,
                   ARRAY_AGG(DISTINCT metadata.`language`) as languages
            FROM `{tenant_id}`
            WHERE repo_id IS NOT MISSING
              AND type IN ['file_index', 'symbol_index', 'module_summary', 'repo_summary']
            GROUP BY repo_id
            ORDER BY doc_count DESC
        """

        result = db.cluster.query(n1ql)

        repos = []
        for row in result:
            # Filter out nulls from languages
            languages = [l for l in (row.get('languages') or []) if l]
            repos.append(RepoInfo(
                repo_id=row['repo_id'],
                doc_count=row['doc_count'],
                languages=languages[:5]  # Top 5 languages
            ))

        logger.info(f"list_repos: found {len(repos)} repositories")
        return repos

    except Exception as e:
        logger.error(f"list_repos failed: {e}")
        return []


async def explore_structure(
    db: CouchbaseClient,
    repos_path: str,
    repo_id: str,
    path: str = "",
    pattern: str = None,
    include_summaries: bool = False,
    tenant_id: str = "code_kosha"
) -> StructureInfo:
    """
    Explore repository directory structure.

    Args:
        db: Couchbase client
        repos_path: Base path where repos are stored on disk
        repo_id: Repository identifier (owner/repo)
        path: Path within repo (empty for root)
        pattern: Optional glob pattern to filter files
        include_summaries: Include module_summary content
        tenant_id: Tenant bucket name

    Returns:
        StructureInfo with directories, files, and key files
    """
    try:
        # Build full path to repo directory
        repo_path = Path(repos_path) / repo_id.replace("/", "_")
        target_path = repo_path / path if path else repo_path

        if not target_path.exists():
            logger.warning(f"Path not found: {target_path}")
            return StructureInfo(
                repo_id=repo_id,
                path=path,
                directories=[],
                files=[],
                key_files={},
                summary=None
            )

        directories = []
        files = []
        key_files = {}

        # List directory contents
        for item in sorted(target_path.iterdir()):
            # Skip hidden files and common ignore patterns
            if item.name.startswith('.') or item.name in ['__pycache__', 'node_modules', '.git', 'venv', '.venv']:
                continue

            rel_path = str(item.relative_to(repo_path))

            if item.is_dir():
                directories.append(item.name + "/")
            elif item.is_file():
                # Apply pattern filter if specified
                if pattern and not fnmatch.fnmatch(item.name, pattern):
                    continue

                ext = item.suffix.lower()
                language = EXTENSION_TO_LANGUAGE.get(ext, "")

                # Count lines
                try:
                    line_count = sum(1 for _ in item.open('r', errors='ignore'))
                except:
                    line_count = 0

                files.append(FileInfo(
                    name=item.name,
                    path=rel_path,
                    language=language,
                    line_count=line_count,
                    has_summary=False  # Will be updated below
                ))

                # Check for key files
                for key_type, patterns in KEY_FILE_PATTERNS.items():
                    for p in patterns:
                        if fnmatch.fnmatch(item.name, p.split('/')[-1]):
                            key_files[key_type] = rel_path
                            break

        # Check which files have summaries in Couchbase
        if files:
            file_paths = [f.path for f in files]
            n1ql = f"""
                SELECT file_path
                FROM `{tenant_id}`
                WHERE type = 'file_index'
                  AND repo_id = $repo_id
                  AND file_path IN $file_paths
            """
            try:
                result = db.cluster.query(
                    n1ql,
                    QueryOptions(named_parameters={"repo_id": repo_id, "file_paths": file_paths})
                )
                indexed_paths = {row['file_path'] for row in result}
                for f in files:
                    f.has_summary = f.path in indexed_paths
            except Exception as e:
                logger.warning(f"Failed to check file summaries: {e}")

        # Get module summary if requested
        summary = None
        if include_summaries and path:
            n1ql = f"""
                SELECT content
                FROM `{tenant_id}`
                WHERE type = 'module_summary'
                  AND repo_id = $repo_id
                  AND module_path = $path
                LIMIT 1
            """
            try:
                result = db.cluster.query(
                    n1ql,
                    QueryOptions(named_parameters={"repo_id": repo_id, "path": path.rstrip('/')})
                )
                for row in result:
                    summary = row.get('content')
                    break
            except Exception as e:
                logger.warning(f"Failed to get module summary: {e}")

        logger.info(f"explore_structure: {repo_id}/{path} - {len(directories)} dirs, {len(files)} files")

        return StructureInfo(
            repo_id=repo_id,
            path=path,
            directories=directories,
            files=files,
            key_files=key_files,
            summary=summary
        )

    except Exception as e:
        logger.error(f"explore_structure failed: {e}")
        return StructureInfo(
            repo_id=repo_id,
            path=path,
            directories=[],
            files=[],
            key_files={},
            summary=None
        )


async def search_code(
    db: CouchbaseClient,
    embedding_model: SentenceTransformer,
    query: str,
    level: SearchLevel = SearchLevel.FILE,
    repo_filter: str = None,
    limit: int = 5,
    tenant_id: str = "code_kosha",
    preview: bool = False
) -> List[SearchResult]:
    """
    Semantic search across indexed documents at specified granularity.

    Args:
        db: Couchbase client
        embedding_model: Sentence transformer for query embedding
        query: Search query (natural language or code)
        level: Granularity level (symbol, file, module, repo, doc)
        repo_filter: Optional repository filter
        limit: Maximum results to return
        tenant_id: Tenant bucket name
        preview: If True, return only metadata without full content (for peek/preview)

    Returns:
        List of SearchResult sorted by relevance
    """
    import httpx

    try:
        doc_type = LEVEL_TO_DOCTYPE[level]

        # Generate query embedding
        query_with_prefix = f"search_document: {query}"
        query_embedding = embedding_model.encode(
            query_with_prefix,
            normalize_embeddings=True
        ).tolist()

        # Build FTS request with hybrid search (query + knn)
        # KNN filter alone doesn't pre-filter in Couchbase - need query + knn_operator: and
        filter_conjuncts = [{"term": doc_type, "field": "type"}]
        if repo_filter:
            filter_conjuncts.append({"term": repo_filter, "field": "repo_id"})

        type_query = filter_conjuncts[0] if len(filter_conjuncts) == 1 else {"conjuncts": filter_conjuncts}

        fts_request = {
            "query": type_query,
            "knn": [{
                "field": "embedding",
                "vector": query_embedding,
                "k": min(limit * 2, 20),  # Oversample
            }],
            "knn_operator": "and",
            "size": min(limit * 2, 20),
            "fields": ["*"]
        }

        # Call Couchbase FTS
        couchbase_host = os.getenv('COUCHBASE_HOST', 'localhost')
        couchbase_user = os.getenv('COUCHBASE_USERNAME', 'Administrator')
        couchbase_pass = os.environ['COUCHBASE_PASSWORD']

        fts_url = f"http://{couchbase_host}:8094/api/index/code_vector_index/query"

        async with httpx.AsyncClient() as client:
            response = await client.post(
                fts_url,
                json=fts_request,
                auth=(couchbase_user, couchbase_pass),
                timeout=30.0
            )

        if response.status_code != 200:
            logger.error(f"FTS search failed: {response.status_code} - {response.text}")
            return []

        fts_results = response.json()
        hits = fts_results.get('hits', [])

        if not hits:
            return []

        # Fetch documents (full or preview mode)
        results = []
        bucket = db.cluster.bucket(tenant_id)
        collection = bucket.default_collection()

        for hit in hits[:limit]:
            doc_id = hit.get('id')
            if not doc_id:
                continue

            try:
                doc_result = collection.get(doc_id)
                doc = doc_result.content_as[dict]
                metadata = doc.get('metadata', {})

                # In preview mode, only return first ~200 chars of content
                content = doc.get('content', '')
                if preview and len(content) > 200:
                    content = content[:200] + "..."

                results.append(SearchResult(
                    document_id=doc_id,
                    doc_type=doc.get('type', doc_type),
                    repo_id=doc.get('repo_id', ''),
                    file_path=doc.get('file_path') or doc.get('module_path'),
                    symbol_name=doc.get('symbol_name'),
                    symbol_type=doc.get('symbol_type') or doc.get('doc_type'),
                    content=content,
                    score=hit.get('score', 0.0),
                    parent_id=doc.get('parent_id'),
                    children_ids=doc.get('children_ids', []),
                    start_line=metadata.get('start_line'),
                    end_line=metadata.get('end_line')
                ))
            except Exception as e:
                logger.warning(f"Failed to fetch document {doc_id}: {e}")
                continue

        logger.info(f"search_code: query='{query[:50]}' level={level.value} preview={preview} found {len(results)} results")
        return results

    except Exception as e:
        logger.error(f"search_code failed: {e}", exc_info=True)
        return []


async def get_file(
    repos_path: str,
    repo_id: str,
    file_path: str,
    start_line: int = None,
    end_line: int = None
) -> Optional[FileContent]:
    """
    Retrieve actual source code from repository.

    Args:
        repos_path: Base path where repos are stored
        repo_id: Repository identifier
        file_path: Path to file relative to repo root
        start_line: Optional start line (1-indexed)
        end_line: Optional end line (1-indexed, inclusive)

    Returns:
        FileContent or None if file not found
    """
    try:
        # Build full path
        repo_path = Path(repos_path) / repo_id.replace("/", "_")
        full_path = repo_path / file_path

        if not full_path.exists():
            logger.warning(f"File not found: {full_path}")
            return None

        # Read file content
        content = full_path.read_text(errors='ignore')
        lines = content.split('\n')
        total_lines = len(lines)

        # Determine line range
        start = start_line or 1
        end = end_line or total_lines

        # Clamp to valid range
        start = max(1, min(start, total_lines))
        end = max(start, min(end, total_lines))

        # Extract lines
        code = '\n'.join(lines[start - 1:end])

        # Detect language
        ext = full_path.suffix.lower()
        language = EXTENSION_TO_LANGUAGE.get(ext, "")

        # Truncate if too large (>100KB)
        max_size = 100_000
        truncated = len(code) > max_size
        if truncated:
            code = code[:max_size] + f"\n\n... [truncated, {len(code) - max_size} chars omitted]"

        logger.info(f"get_file: {repo_id}/{file_path} lines {start}-{end}/{total_lines}")

        return FileContent(
            repo_id=repo_id,
            file_path=file_path,
            code=code,
            start_line=start,
            end_line=end,
            total_lines=total_lines,
            language=language,
            truncated=truncated
        )

    except Exception as e:
        logger.error(f"get_file failed: {e}")
        return None
