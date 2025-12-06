"""
Git operations for incremental updates.
"""

import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from .models import ChangeSet


class GitOperations:
    """Git operations helper for incremental updates."""

    @staticmethod
    def fetch(repo_path: Path) -> bool:
        """Fetch latest from origin"""
        try:
            result = subprocess.run(
                ['git', 'fetch', 'origin'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Git fetch failed: {e}")
            return False

    @staticmethod
    def pull(repo_path: Path) -> bool:
        """Pull latest changes (fast-forward only)"""
        try:
            result = subprocess.run(
                ['git', 'pull', '--ff-only'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=120
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Git pull failed: {e}")
            return False

    @staticmethod
    def get_head_commit(repo_path: Path, ref: str = 'HEAD') -> Optional[str]:
        """Get commit hash for a ref"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', ref],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception:
            return None

    @staticmethod
    def get_origin_head(repo_path: Path, branch: str = None) -> Optional[str]:
        """Get origin's HEAD commit (uses default branch from origin/HEAD)"""
        if branch is None:
            branch = GitOperations.get_default_branch(repo_path)

        for b in [branch, 'main', 'master']:
            commit = GitOperations.get_head_commit(repo_path, f'origin/{b}')
            if commit:
                return commit
        return None

    @staticmethod
    def get_default_branch(repo_path: Path) -> str:
        """Detect default branch from origin/HEAD"""
        try:
            result = subprocess.run(
                ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                # Output is like "refs/remotes/origin/aj/Lightsail_deployment"
                # Remove the prefix to get the branch name (may contain slashes)
                ref = result.stdout.strip()
                prefix = 'refs/remotes/origin/'
                if ref.startswith(prefix):
                    return ref[len(prefix):]
                # Fallback: take everything after last 'origin/'
                if 'origin/' in ref:
                    return ref.split('origin/', 1)[1]
        except Exception:
            pass
        return 'main'

    @staticmethod
    def get_changed_files(repo_path: Path, old_commit: str, new_commit: str) -> ChangeSet:
        """Get list of changed files between two commits"""
        try:
            result = subprocess.run(
                ['git', 'diff', '--name-status', old_commit, new_commit],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.warning(f"Git diff failed: {result.stderr}")
                return ChangeSet([], [], [])

            added, modified, deleted = [], [], []

            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                parts = line.split('\t')
                status = parts[0]

                if status.startswith('A'):
                    added.append(parts[1])
                elif status.startswith('M'):
                    modified.append(parts[1])
                elif status.startswith('D'):
                    deleted.append(parts[1])
                elif status.startswith('R'):
                    # Rename = delete old + add new
                    deleted.append(parts[1])
                    added.append(parts[2])
                elif status.startswith('C'):
                    # Copy = just add new
                    added.append(parts[2])

            return ChangeSet(added, modified, deleted)

        except Exception as e:
            logger.error(f"Error getting changed files: {e}")
            return ChangeSet([], [], [])

    @staticmethod
    def get_file_diff(
        repo_path: Path,
        old_commit: str,
        new_commit: str,
        file_path: str
    ) -> str:
        """Get the diff for a specific file between two commits"""
        try:
            result = subprocess.run(
                ['git', 'diff', old_commit, new_commit, '--', file_path],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )
            return result.stdout[:2000] if result.returncode == 0 else ""
        except Exception:
            return ""

    @staticmethod
    def clone(repo_id: str, target_path: Path, github_token: Optional[str] = None) -> bool:
        """Clone a repo from GitHub"""
        if target_path.exists():
            logger.debug(f"Repo already exists: {target_path}")
            return True

        try:
            # Construct clone URL
            if github_token:
                clone_url = f"https://{github_token}@github.com/{repo_id}.git"
            else:
                clone_url = f"https://github.com/{repo_id}.git"

            logger.info(f"Cloning {repo_id}...")
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', clone_url, str(target_path)],
                capture_output=True,
                text=True,
                timeout=300  # 5 min timeout
            )

            if result.returncode != 0:
                logger.error(f"Clone failed for {repo_id}: {result.stderr}")
                return False

            logger.info(f"Cloned {repo_id} to {target_path}")
            return True

        except subprocess.TimeoutExpired:
            logger.error(f"Clone timed out for {repo_id}")
            return False
        except Exception as e:
            logger.error(f"Clone failed for {repo_id}: {e}")
            return False
