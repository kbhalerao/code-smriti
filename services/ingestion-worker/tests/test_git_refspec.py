"""Regression tests for clones taken while the remote was empty.

`git clone` of an empty repository writes a [remote "origin"] stanza with no
`fetch` line, because there is no branch for a single-branch refspec to name.
Fetches in such a clone write FETCH_HEAD and create no remote-tracking ref, so
origin/<branch> never resolves — even after the remote gains commits. One repo
sat in that state re-downloading 71 files on every run while holding zero
documents in the corpus.

These use local bare repos, so they need no network and no credentials.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v4.incremental.git_utils import GitOperations  # noqa: E402


def _git(cwd, *args):
    return subprocess.run(
        ['git', *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def empty_clone(tmp_path):
    """A clone taken while origin had no branches — the shape that breaks."""
    origin = tmp_path / 'origin.git'
    _git(tmp_path, 'init', '--bare', '-q', str(origin))

    clone = tmp_path / 'clone'
    _git(tmp_path, 'clone', '--depth', '1', '-q', str(origin), str(clone))

    # Guard the premise: if git ever starts writing a refspec here, these tests
    # are asserting against a bug that no longer exists and should be revisited.
    refspec = subprocess.run(
        ['git', 'config', '--get', 'remote.origin.fetch'],
        cwd=str(clone), capture_output=True, text=True
    )
    assert refspec.stdout.strip() == '', 'clone of an empty repo now writes a refspec'

    return origin, clone


def _push_commit(tmp_path, origin, message='initial'):
    seed = tmp_path / 'seed'
    _git(tmp_path, 'clone', '-q', str(origin), str(seed))
    (seed / 'file.py').write_text('x = 1\n')
    _git(seed, 'add', 'file.py')
    _git(seed, '-c', 'user.email=t@t', '-c', 'user.name=t', 'commit', '-q', '-m', message)
    _git(seed, 'push', '-q', 'origin', 'HEAD:main')
    return _git(seed, 'rev-parse', 'HEAD').stdout.strip()


def test_fetch_repairs_missing_refspec(empty_clone):
    _, clone = empty_clone

    assert GitOperations.fetch(clone) is True

    refspec = subprocess.run(
        ['git', 'config', '--get', 'remote.origin.fetch'],
        cwd=str(clone), capture_output=True, text=True
    )
    assert refspec.stdout.strip() == '+refs/heads/*:refs/remotes/origin/*'


def test_ensure_fetch_refspec_leaves_a_narrow_refspec_alone(tmp_path):
    """Shallow clones legitimately pin one branch; do not widen them."""
    origin = tmp_path / 'origin.git'
    _git(tmp_path, 'init', '--bare', '-q', str(origin))
    _push_commit(tmp_path, origin)

    clone = tmp_path / 'clone'
    _git(tmp_path, 'clone', '--depth', '1', '--single-branch', '-q', str(origin), str(clone))
    before = _git(clone, 'config', '--get', 'remote.origin.fetch').stdout.strip()
    assert before == '+refs/heads/main:refs/remotes/origin/main'

    GitOperations._ensure_fetch_refspec(clone)

    after = _git(clone, 'config', '--get', 'remote.origin.fetch').stdout.strip()
    assert after == before


def test_sync_recovers_once_the_empty_remote_gains_commits(tmp_path, empty_clone):
    """The case that cost a repo: cloned empty, populated later, never indexed."""
    origin, clone = empty_clone
    head = _push_commit(tmp_path, origin)

    assert GitOperations.fetch(clone) is True
    assert GitOperations.sync_to_default_branch(clone) == head
    assert (clone / 'file.py').exists()


def test_still_empty_remote_reports_no_branches(empty_clone):
    _, clone = empty_clone

    GitOperations.fetch(clone)

    assert GitOperations.remote_has_branches(clone) is False
    assert GitOperations.sync_to_default_branch(clone) is None


def test_remote_has_branches_is_none_when_origin_is_unreachable(tmp_path, empty_clone):
    """Unreachable must not be mistaken for empty, or a network blip would
    reclassify a live repo as never-populated."""
    origin, clone = empty_clone
    _git(clone, 'remote', 'set-url', 'origin', str(tmp_path / 'gone.git'))

    assert GitOperations.remote_has_branches(clone) is None
