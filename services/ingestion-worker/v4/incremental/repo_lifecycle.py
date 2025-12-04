"""
Repository lifecycle management: discovery, cloning, deletion.
"""

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Set, Union

from loguru import logger


class RepoLifecycle:
    """Manages repository discovery and lifecycle."""

    def __init__(self, repos_path: Path, cb_client, github_token: Optional[str] = None):
        self.repos_path = repos_path
        self.cb_client = cb_client
        self.github_token = github_token

    # =========================================================================
    # Repository Discovery
    # =========================================================================

    def get_canonical_repo_list(self) -> List[str]:
        """
        Get the canonical list of repos that should be indexed.

        Sources (in order of preference):
        1. GitHub API (if token is set)
        2. repos_to_ingest.txt config file
        3. Existing repos on disk (fallback)
        """
        # Try GitHub API first
        if self.github_token:
            repos = self._get_repos_from_github()
            if repos:
                logger.info(f"Got {len(repos)} repos from GitHub API")
                return repos

        # Try config file
        config_file = self.repos_path.parent / "repos_to_ingest.txt"
        if config_file.exists():
            repos = self._get_repos_from_config(config_file)
            if repos:
                logger.info(f"Got {len(repos)} repos from config file")
                return repos

        # Fallback to disk
        logger.warning("No GitHub token or config file - using repos on disk")
        return [r['repo_id'] for r in self.discover_repos_on_disk()]

    def _get_repos_from_github(self) -> List[str]:
        """Query GitHub API for user's repos"""
        try:
            import httpx

            headers = {
                "Authorization": f"token {self.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }

            repos = []
            page = 1

            while True:
                response = httpx.get(
                    f"https://api.github.com/user/repos?per_page=100&page={page}",
                    headers=headers,
                    timeout=30
                )

                if response.status_code != 200:
                    logger.error(f"GitHub API error: {response.status_code}")
                    return []

                data = response.json()
                if not data:
                    break

                for repo in data:
                    repos.append(repo['full_name'])

                page += 1

            return repos

        except Exception as e:
            logger.error(f"Failed to query GitHub API: {e}")
            return []

    def _get_repos_from_config(self, config_file: Path) -> List[str]:
        """Read repos from config file (one per line, # comments)"""
        repos = []
        try:
            with open(config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    repo_id = line.split('#')[0].strip()
                    if repo_id and '/' in repo_id:
                        repos.append(repo_id)
        except Exception as e:
            logger.error(f"Failed to read config file: {e}")
        return repos

    def discover_repos_on_disk(self) -> List[Dict[str, str]]:
        """Discover all repos currently on disk"""
        if not self.repos_path.exists():
            logger.error(f"Repos path does not exist: {self.repos_path}")
            return []

        repositories = []
        for repo_dir in sorted(self.repos_path.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name.startswith('.'):
                continue

            # Convert folder name to repo_id: owner_repo -> owner/repo
            parts = repo_dir.name.split('_', 1)
            if len(parts) == 2:
                repo_id = f"{parts[0]}/{parts[1]}"
                repositories.append({
                    'repo_id': repo_id,
                    'repo_path': str(repo_dir)
                })

        return repositories

    def get_repos_in_database(self) -> Set[str]:
        """Get all repo_ids that have documents in Couchbase"""
        try:
            query = """
                SELECT DISTINCT repo_id
                FROM `code_kosha`
                WHERE repo_id IS NOT MISSING
            """
            result = self.cb_client.cluster.query(query)
            return {row['repo_id'] for row in result}
        except Exception as e:
            logger.error(f"Failed to query repos from database: {e}")
            return set()

    # =========================================================================
    # Path Utilities
    # =========================================================================

    def repo_id_to_path(self, repo_id: str) -> Path:
        """Convert repo_id to disk path: owner/repo -> owner_repo"""
        folder_name = repo_id.replace('/', '_')
        return self.repos_path / folder_name

    # =========================================================================
    # Deletion
    # =========================================================================

    def delete_repo_docs(self, repo_id: str, dry_run: bool = False) -> int:
        """Delete all documents for a repo from Couchbase"""
        if dry_run:
            logger.info(f"[DRY RUN] Would delete all docs for {repo_id}")
            return 0

        try:
            query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            metrics = result.metadata().metrics()
            deleted = metrics.mutation_count() if metrics else 0
            logger.info(f"Deleted {deleted} documents for {repo_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete docs for {repo_id}: {e}")
            return 0

    def delete_file_docs(self, repo_id: str, file_path: str, dry_run: bool = False) -> int:
        """Delete all documents for a specific file"""
        if dry_run:
            logger.info(f"  [DRY RUN] Would delete docs for {file_path}")
            return 0

        try:
            from couchbase.options import QueryOptions
            query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND file_path = $file_path
                  AND type IN ['file_index', 'symbol_index']
            """
            result = self.cb_client.cluster.query(
                query,
                QueryOptions(named_parameters={"repo_id": repo_id, "file_path": file_path})
            )
            # Consume results to ensure query completes
            _ = list(result)
            # Now metadata is available
            try:
                metrics = result.metadata().metrics()
                return metrics.mutation_count() if metrics else 0
            except Exception:
                return 0  # Metrics not available, but delete likely succeeded
        except Exception as e:
            logger.error(f"Error deleting docs for {file_path}: {e}")
            return 0

    def delete_doc_chunks(self, repo_id: str, file_path: str, dry_run: bool = False) -> int:
        """Delete document chunks for a documentation file"""
        if dry_run:
            return 0

        try:
            from couchbase.options import QueryOptions
            query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND file_path = $file_path
                  AND type = 'document'
            """
            result = self.cb_client.cluster.query(
                query,
                QueryOptions(named_parameters={"repo_id": repo_id, "file_path": file_path})
            )
            # Consume results to ensure query completes
            _ = list(result)
            try:
                metrics = result.metadata().metrics()
                return metrics.mutation_count() if metrics else 0
            except Exception:
                return 0
        except Exception as e:
            logger.error(f"Error deleting doc chunks for {file_path}: {e}")
            return 0

    def delete_repo_from_disk(self, repo_id: str, dry_run: bool = False) -> bool:
        """Delete a repo from disk"""
        repo_path = self.repo_id_to_path(repo_id)

        if not repo_path.exists():
            return True

        if dry_run:
            logger.info(f"[DRY RUN] Would delete {repo_path}")
            return True

        try:
            shutil.rmtree(repo_path)
            logger.info(f"Deleted {repo_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete {repo_path}: {e}")
            return False

    # =========================================================================
    # Database Queries
    # =========================================================================

    def get_stored_commit(self, repo_id: str) -> Optional[str]:
        """Get stored commit hash from repo_summary document"""
        try:
            query = """
                SELECT commit_hash
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type = 'repo_summary'
                LIMIT 1
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            for row in result:
                return row.get('commit_hash')
            return None
        except Exception as e:
            logger.warning(f"Could not get stored commit for {repo_id}: {e}")
            return None

    def get_repo_file_count(self, repo_id: str) -> int:
        """Get total file_index count for threshold calculation"""
        try:
            query = """
                SELECT COUNT(*) as count
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type = 'file_index'
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            for row in result:
                return row.get('count', 0)
            return 0
        except Exception:
            return 0

    def get_old_file_summary(self, repo_id: str, file_path: str) -> Optional[str]:
        """Fetch existing file_index content (summary) from database"""
        try:
            from couchbase.options import QueryOptions
            query = """
                SELECT content
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND file_path = $file_path
                  AND type = 'file_index'
                LIMIT 1
            """
            result = self.cb_client.cluster.query(
                query,
                QueryOptions(named_parameters={"repo_id": repo_id, "file_path": file_path})
            )
            for row in result:
                return row.get('content', '')
            return None
        except Exception:
            return None

    def get_old_repo_summary(self, repo_id: str) -> Optional[str]:
        """Fetch existing repo_summary from database"""
        try:
            query = """
                SELECT summary
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type = 'repo_summary'
                LIMIT 1
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            for row in result:
                return row.get('summary', '')
            return None
        except Exception:
            return None

    def get_old_file_embedding(self, repo_id: str, file_path: str) -> Optional[List[float]]:
        """Fetch existing file_index embedding from database for similarity comparison"""
        try:
            from couchbase.options import QueryOptions
            query = """
                SELECT embedding
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND file_path = $file_path
                  AND type = 'file_index'
                LIMIT 1
            """
            result = self.cb_client.cluster.query(
                query,
                QueryOptions(named_parameters={"repo_id": repo_id, "file_path": file_path})
            )
            for row in result:
                return row.get('embedding')
            return None
        except Exception:
            return None
