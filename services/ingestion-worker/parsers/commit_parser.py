"""
Commit Parser - Extracts and stores git commit metadata
Creates one document per unique commit for searchability and deduplication
"""

import hashlib
from typing import Dict, List, Set
from datetime import datetime
from pathlib import Path

from loguru import logger
import git

from config import WorkerConfig

config = WorkerConfig()


class CommitChunk:
    """Represents a git commit with metadata"""

    def __init__(
        self,
        repo_id: str,
        commit_hash: str,
        commit_date: str,
        author: str,
        commit_message: str,
        files_changed: List[str] = None
    ):
        # Chunk ID is simply a hash of repo:commit
        # This ensures one document per unique commit
        chunk_key = f"{repo_id}:{commit_hash}"
        self.chunk_id = hashlib.sha256(chunk_key.encode()).hexdigest()

        self.type = "commit"
        self.repo_id = repo_id
        self.commit_hash = commit_hash
        self.commit_date = commit_date
        self.author = author
        self.commit_message = commit_message
        self.files_changed = files_changed or []
        self.created_at = datetime.utcnow().isoformat()

        # Commits don't have embeddings by default
        # But you could embed commit messages for semantic search
        self.embedding = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage"""
        return {
            "chunk_id": self.chunk_id,
            "type": self.type,
            "repo_id": self.repo_id,
            "commit_hash": self.commit_hash,
            "commit_date": self.commit_date,
            "author": self.author,
            "content": self.commit_message,  # Unified schema: commit_message -> content
            "files_changed": self.files_changed,
            "embedding": self.embedding,
            "created_at": self.created_at
        }


class CommitParser:
    """
    Extracts unique commits from a repository
    """

    def __init__(self):
        """Initialize commit parser"""
        logger.info("Initializing commit parser")

    def extract_commits_from_chunks(
        self,
        chunks: List,
        repo_id: str
    ) -> List[CommitChunk]:
        """
        Extract unique commits from parsed code/doc chunks

        Args:
            chunks: List of CodeChunk or DocumentChunk objects
            repo_id: Repository identifier

        Returns:
            List of unique CommitChunk objects
        """
        unique_commits = {}  # commit_hash -> commit data

        for chunk in chunks:
            commit_hash = chunk.metadata.get("commit_hash")

            # Skip if no commit or already seen
            if not commit_hash or commit_hash == "no_commit" or commit_hash in unique_commits:
                continue

            # Extract commit metadata from chunk
            unique_commits[commit_hash] = {
                "commit_hash": commit_hash,
                "commit_date": chunk.metadata.get("commit_date", ""),
                "author": chunk.metadata.get("author", ""),
                "commit_message": chunk.metadata.get("commit_message", ""),
                "files": set([chunk.file_path])
            }

        # Create CommitChunk objects
        commit_chunks = []
        for commit_hash, data in unique_commits.items():
            commit_chunks.append(CommitChunk(
                repo_id=repo_id,
                commit_hash=commit_hash,
                commit_date=data["commit_date"],
                author=data["author"],
                commit_message=data["commit_message"],
                files_changed=list(data["files"])
            ))

        logger.info(f"Extracted {len(commit_chunks)} unique commits from {len(chunks)} chunks")
        return commit_chunks

    def extract_all_commits(
        self,
        repo_path: Path,
        repo_id: str,
        max_commits: int = None
    ) -> List[CommitChunk]:
        """
        Extract ALL commits from repository history
        Use this for comprehensive commit indexing

        Args:
            repo_path: Path to the repository
            repo_id: Repository identifier
            max_commits: Optional limit on number of commits to extract

        Returns:
            List of CommitChunk objects
        """
        try:
            repo = git.Repo(repo_path)
            commits = list(repo.iter_commits(max_count=max_commits))

            commit_chunks = []
            for commit in commits:
                # Get files changed in this commit
                files_changed = []
                try:
                    if commit.parents:
                        # Compare with parent to get changed files
                        diff = commit.parents[0].diff(commit)
                        files_changed = [item.a_path for item in diff]
                except Exception as e:
                    logger.debug(f"Could not get files for commit {commit.hexsha[:8]}: {e}")

                commit_chunks.append(CommitChunk(
                    repo_id=repo_id,
                    commit_hash=commit.hexsha,
                    commit_date=commit.committed_datetime.isoformat(),
                    author=commit.author.email,
                    commit_message=commit.message.strip(),
                    files_changed=files_changed
                ))

            logger.info(f"Extracted {len(commit_chunks)} commits from repository history")
            return commit_chunks

        except Exception as e:
            logger.error(f"Error extracting commits from {repo_path}: {e}")
            return []
