"""rotate_log: scan and drain share tick.log, so it can be entered concurrently."""
import gzip
import os
import subprocess
import time
from pathlib import Path

ROTATE = Path(__file__).parent.parent / "_logrotate.sh"
LINE = "x" * 99 + "\n"


def rotate(log: Path, max_bytes: int = 1000, keep: int = 5) -> subprocess.Popen:
    return subprocess.Popen(
        ["bash", "-c", f'source "{ROTATE}"; rotate_log "$1" {max_bytes} {keep}', "_", str(log)]
    )


def archives(log: Path) -> list:
    return sorted(log.parent.glob("tick.*.log.gz"))


def make_log(tmp_path: Path, lines: int = 200_000) -> Path:
    log = tmp_path / "tick.log"
    log.write_text(LINE * lines)
    return log


def test_rotates_an_oversized_log_and_keeps_its_content(tmp_path):
    log = make_log(tmp_path, lines=100)
    assert rotate(log).wait() == 0

    [archive] = archives(log)
    assert gzip.decompress(archive.read_bytes()).decode() == LINE * 100
    assert "rotated" in log.read_text()
    assert LINE not in log.read_text()
    assert not (tmp_path / "tick.log.rotate.lock").exists()


def test_leaves_a_small_log_alone(tmp_path):
    log = tmp_path / "tick.log"
    log.write_text("small\n")
    assert rotate(log).wait() == 0
    assert log.read_text() == "small\n"
    assert archives(log) == []


def test_concurrent_rotations_produce_one_complete_archive(tmp_path):
    log = make_log(tmp_path)
    procs = [rotate(log) for _ in range(6)]
    assert [p.wait() for p in procs] == [0] * 6

    # Later entrants may find the truncated log under the threshold, or the lock
    # held; either way nothing is lost and exactly one archive holds the content.
    [archive] = archives(log)
    assert gzip.decompress(archive.read_bytes()).decode() == LINE * 200_000
    assert list(tmp_path.glob("*.partial")) == []


def test_a_held_lock_skips_rotation(tmp_path):
    log = make_log(tmp_path, lines=100)
    (tmp_path / "tick.log.rotate.lock").mkdir()
    assert rotate(log).wait() == 0
    assert archives(log) == []
    assert log.read_text() == LINE * 100


def test_a_stale_lock_does_not_stop_rotation_for_good(tmp_path):
    log = make_log(tmp_path, lines=100)
    lock = tmp_path / "tick.log.rotate.lock"
    lock.mkdir()
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert rotate(log).wait() == 0
    assert len(archives(log)) == 1


def test_prunes_to_keep(tmp_path):
    log = make_log(tmp_path, lines=100)
    for day in range(1, 5):
        (tmp_path / f"tick.2026-01-0{day}_00-00-00.log.gz").write_bytes(b"")
    assert rotate(log, keep=2).wait() == 0
    remaining = [a.name for a in archives(log)]
    assert len(remaining) == 2
    assert "tick.2026-01-04_00-00-00.log.gz" in remaining
