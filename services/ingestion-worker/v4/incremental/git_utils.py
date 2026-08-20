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
    def _run(repo_path: Path, args: list, timeout: int = 60) -> subprocess.CompletedProcess:
        """Run a git command in repo_path. Callers handle failure."""
        return subprocess.run(
            args,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout
        )

    @staticmethod
    def fetch(repo_path: Path) -> bool:
        """Fetch latest from origin, and re-point origin/HEAD at the remote's
        current default branch.

        `git fetch` never updates origin/HEAD — it is written once at clone time
        and then frozen. When a project retires a branch (labcore2024 ->
        labcore2026), every consumer of get_default_branch() keeps naming the
        dead one until someone runs set-head by hand. Refreshing it here costs
        one ls-remote per repo on a call that already talks to the network.
        """
        try:
            result = GitOperations._run(repo_path, ['git', 'fetch', 'origin'])
            if result.returncode != 0:
                logger.warning(f"Git fetch failed: {result.stderr.strip()}")
                return False

            # Non-fatal: a repo with no default branch on the remote still fetches.
            head = GitOperations._run(repo_path, ['git', 'remote', 'set-head', 'origin', '-a'])
            if head.returncode != 0:
                logger.debug(f"Could not refresh origin/HEAD: {head.stderr.strip()}")

            return True
        except Exception as e:
            logger.warning(f"Git fetch failed: {e}")
            return False

    @staticmethod
    def sync_to_default_branch(repo_path: Path) -> Optional[str]:
        """Put the worktree on origin's default branch at origin's tip.

        Returns the resulting HEAD commit, or None if the sync failed.

        Replaces `git pull --ff-only`, which was not sufficient: pull acts on
        whichever branch happens to be checked out, while get_origin_head()
        reads the *default* branch named by origin/HEAD. A clone parked on a
        stale branch therefore had its change list diffed from the default
        branch and its file contents read from the stale one, and the run then
        recorded the default branch's commit — so the next run compared equal
        and never looked again. Self-sealing, and silent.

        Found 2026-08-20 in three clones. labcore had been serving a January
        branch of a repo whose trunk was 336 commits and 264K deleted lines
        ahead, under an August commit hash. Two more (gmslab, ListingsAISearch)
        had drifted the same way.

        `checkout --force -B` creates-or-resets the local branch onto the remote
        ref and switches to it in one step; `reset --hard` then discards any
        in-place modification the checkout had no reason to rewrite.
        That is safe here because these clones are a machine-managed cache: a
        reflog sweep over all 297 found zero locally-authored commits — every
        entry was `clone:` or `pull:`. It is deliberately unconditional rather
        than dry_run-gated, because a worktree on the wrong branch makes a dry
        run's report wrong too, and the worktree is derived state, not input.
        """
        branch = GitOperations.get_default_branch(repo_path)
        remote_ref = f'origin/{branch}'

        try:
            # Single-branch and shallow clones carry a narrow refspec, so the
            # default branch may not be present locally at all once origin/HEAD
            # moves outside it. Fetch it by name before reaching for it.
            if GitOperations.get_head_commit(repo_path, remote_ref) is None:
                GitOperations._run(repo_path, ['git', 'fetch', 'origin', branch], timeout=120)

            if GitOperations.get_head_commit(repo_path, remote_ref) is None:
                logger.error(f"Cannot sync {repo_path.name}: {remote_ref} does not resolve")
                return None

            before_branch = GitOperations._run(
                repo_path, ['git', 'branch', '--show-current']
            ).stdout.strip() or '(detached)'
            before = GitOperations.get_head_commit(repo_path)

            result = GitOperations._run(
                repo_path,
                ['git', 'checkout', '--force', '-B', branch, remote_ref],
                timeout=120
            )
            if result.returncode != 0:
                logger.error(f"Cannot sync {repo_path.name} to {remote_ref}: {result.stderr.strip()}")
                return None

            # checkout --force only rewrites files that differ between the two
            # branches, so a tracked file modified in place while already on the
            # target branch survives it. reset --hard makes the invariant real:
            # the worktree matches the commit this run is about to record.
            reset = GitOperations._run(
                repo_path, ['git', 'reset', '--hard', remote_ref], timeout=120
            )
            if reset.returncode != 0:
                logger.error(f"Cannot reset {repo_path.name} to {remote_ref}: {reset.stderr.strip()}")
                return None

            after = GitOperations.get_head_commit(repo_path)

            # Loud on purpose: drift is invisible otherwise, which is how it
            # survived from January to August.
            if before_branch != branch:
                logger.warning(
                    f"  Worktree re-anchored: {before_branch}@{(before or '?')[:8]} "
                    f"-> {branch}@{(after or '?')[:8]}"
                )
            elif before != after:
                logger.info(f"  Advanced {branch}: {(before or '?')[:8]} -> {(after or '?')[:8]}")

            return after

        except Exception as e:
            logger.error(f"Git sync failed for {repo_path.name}: {e}")
            return None

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
