"""
Git operations for incremental updates.
"""

import os
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from .models import ChangeSet


# Credentials reach git through the environment, never through the URL.
#
# `clone` used to interpolate the token into the clone URL. git persists that
# into .git/config, so one secret ended up copied into every repository on disk
# (296 of them, found 2026-08-21) and every later `fetch` silently depended on
# it being there — which is why rotating the token alone fixed nothing: the next
# clone wrote the new one straight back out.
#
# A credential helper supplied via GIT_CONFIG_* keeps the token out of the
# config file and out of argv; only this string is visible in `ps`, and it names
# the variable rather than holding the value. Repositories cloned before this
# change still carry a token in their remote URL and keep working untouched —
# git prefers URL credentials when they are present — so this can land without
# rewriting them.
_CREDENTIAL_HELPER = '!f(){ echo username=x-access-token; echo "password=$GITHUB_TOKEN"; };f'


def _git_env(github_token: Optional[str] = None) -> dict:
    """Environment for a git subprocess, with credentials bound to a helper.

    Falls back to an ambient GITHUB_TOKEN so fetches work on the scheduled path,
    where run_incremental.sh has already sourced .env, as well as on manual runs
    where the caller passes the token from config.
    """
    env = os.environ.copy()
    token = github_token or env.get("GITHUB_TOKEN", "")
    if token:
        env["GITHUB_TOKEN"] = token
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "credential.https://github.com.helper"
        env["GIT_CONFIG_VALUE_0"] = _CREDENTIAL_HELPER
    return env


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
            timeout=timeout,
            env=_git_env()
        )

    @staticmethod
    def _ensure_fetch_refspec(repo_path: Path) -> None:
        """Guarantee origin carries a fetch refspec.

        `git clone` of an *empty* repository writes a [remote "origin"] stanza
        holding a url and no `fetch` line at all — with no branches advertised
        there is nothing for a single-branch refspec to name. Fetches in such a
        clone then download objects and write FETCH_HEAD without creating any
        remote-tracking ref, so origin/<branch> never resolves however many
        times they run, and the repo stays unindexable even after the remote
        gains commits.

        Found 2026-08-22 in four clones. Five of the six repos failing to sync
        were simply still empty upstream, but kbhalerao/agkit.io-romex had since
        received 71 files and was re-downloading them into an unreferenced
        object store on every run while holding zero documents in the corpus.
        """
        existing = GitOperations._run(
            repo_path, ['git', 'config', '--get', 'remote.origin.fetch']
        )
        if existing.returncode == 0 and existing.stdout.strip():
            return

        result = GitOperations._run(
            repo_path,
            ['git', 'config', 'remote.origin.fetch',
             '+refs/heads/*:refs/remotes/origin/*']
        )
        if result.returncode == 0:
            logger.warning(
                f"  Repaired missing fetch refspec on {repo_path.name} "
                f"(cloned while the remote was empty)"
            )

    @staticmethod
    def remote_has_branches(repo_path: Path) -> Optional[bool]:
        """Whether origin advertises any branch. None if origin is unreachable.

        A local ref lookup cannot tell "nothing has been pushed yet" apart from
        "the sync is broken", and the two deserve different log levels.
        """
        result = GitOperations._run(
            repo_path, ['git', 'ls-remote', '--heads', 'origin'], timeout=60
        )
        if result.returncode != 0:
            return None
        return bool(result.stdout.strip())

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
            GitOperations._ensure_fetch_refspec(repo_path)

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
            # moves outside it. Fetch it by name before reaching for it, naming
            # the destination explicitly: `git fetch origin <branch>` relies on
            # remote.origin.fetch to decide where the ref lands, and a clone
            # taken while the remote was empty has no such setting.
            if GitOperations.get_head_commit(repo_path, remote_ref) is None:
                GitOperations._ensure_fetch_refspec(repo_path)
                GitOperations._run(
                    repo_path,
                    ['git', 'fetch', 'origin',
                     f'+refs/heads/{branch}:refs/remotes/origin/{branch}'],
                    timeout=120
                )

            if GitOperations.get_head_commit(repo_path, remote_ref) is None:
                if GitOperations.remote_has_branches(repo_path) is False:
                    logger.info(f"  Skipping {repo_path.name}: remote has no branches yet")
                else:
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
            # Plain URL: the token is supplied by the credential helper in
            # _git_env, so it is never written into the new repo's config.
            clone_url = f"https://github.com/{repo_id}.git"

            logger.info(f"Cloning {repo_id}...")
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', clone_url, str(target_path)],
                capture_output=True,
                text=True,
                timeout=300,  # 5 min timeout
                env=_git_env(github_token)
            )

            if result.returncode != 0:
                logger.error(f"Clone failed for {repo_id}: {result.stderr}")
                return False

            # An empty remote yields a clone with no fetch refspec at all;
            # write one now rather than leaving the repo unfetchable until some
            # later run notices. See _ensure_fetch_refspec.
            GitOperations._ensure_fetch_refspec(target_path)

            logger.info(f"Cloned {repo_id} to {target_path}")
            return True

        except subprocess.TimeoutExpired:
            logger.error(f"Clone timed out for {repo_id}")
            return False
        except Exception as e:
            logger.error(f"Clone failed for {repo_id}: {e}")
            return False
