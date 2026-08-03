# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the per-repo reconcile mutation lock (`reconcile.lock`)."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from context_lifecycle.reconcile import lock as lock_mod
from context_lifecycle.reconcile.lock import (
    LOCK_RELPATH,
    PruneLockHeld,
    reconcile_lock,
)
from context_lifecycle.reconcile.prune import apply_plan, build_plan


def _checksum(p: Path) -> str:
    return sha256(p.read_bytes()).hexdigest()


def _setup_repo(tmp_path) -> Path:
    repo = tmp_path / "PilotRepo"
    console = repo / ".console"
    console.mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("design doc", encoding="utf-8")
    (console / "reconcile.yaml").write_text(
        "repo: PilotRepo\n"
        "items:\n"
        "  - id: detectors-trio\n"
        "    title: 'Detectors trio'\n"
        "    status: done\n"
        "    owner: PilotRepo\n"
        "    doc: [docs/design.md]\n",
        encoding="utf-8",
    )
    (console / "log.md").write_text(
        "# Log\n\n## 2026-05-30 — detectors trio shipped\n\nCompleted.\n\n",
        encoding="utf-8",
    )
    (console / "backlog.md").write_text(
        "# Backlog\n\n## In Progress\n\n- [ ] active thing\n\n"
        "## Done\n\n- [x] detectors trio\n\n",
        encoding="utf-8",
    )
    return repo


def test_lock_is_exclusive_and_reentrant_after_release(tmp_path):
    """A held lock blocks a second acquire; release makes it reacquirable."""
    repo = tmp_path
    (repo / ".console").mkdir()
    entered = 0
    with reconcile_lock(repo):
        entered += 1
        with pytest.raises(PruneLockHeld), reconcile_lock(repo):
            pytest.fail("second acquire must not enter the block")
    with reconcile_lock(repo):  # released → reacquirable
        entered += 1
    assert entered == 2
    assert (repo / LOCK_RELPATH).is_file()  # lock file persists (diagnostics)


def test_apply_refused_while_lock_held(tmp_path, monkeypatch):
    """A second --apply against a locked repo fails closed (no interleaving)."""
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    repo = _setup_repo(tmp_path)
    private = tmp_path / "PrivateSide"
    plan = build_plan(repo, private_root=private)

    log_before = _checksum(repo / ".console" / "log.md")
    with reconcile_lock(repo), pytest.raises(PruneLockHeld):  # another applier
        apply_plan(repo, plan)
    # Refused apply mutated nothing.
    assert _checksum(repo / ".console" / "log.md") == log_before
    assert not private.exists()


def test_lock_released_after_apply(tmp_path, monkeypatch):
    """The lock is held only for the duration of apply — reacquirable after."""
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    repo = _setup_repo(tmp_path)
    private = tmp_path / "PrivateSide"
    plan = build_plan(repo, private_root=private)
    applied = apply_plan(repo, plan)
    assert applied.applied

    reacquired = False
    with reconcile_lock(repo):  # would raise PruneLockHeld if apply leaked it
        reacquired = True
    assert reacquired


# ── cross-platform backend ───────────────────────────────────────────────────
# Until 2026-08-03 this module raised on any non-POSIX host, so `prune --apply`
# was unrunnable on Windows. These pin that both backends actually lock, rather
# than that the module merely imports.


def test_a_backend_is_available_on_this_platform():
    """No supported platform may fall through to the 'neither backend' error."""
    assert (lock_mod.fcntl is not None) or (lock_mod.msvcrt is not None)


def test_holder_pid_is_recorded_and_readable_while_held(tmp_path):
    """The pid field must stay readable while the lock is held.

    On Windows the locked range is mandatory, so this fails if the sentinel byte
    is ever moved on top of the pid field — a contending run could then not name
    the holder.
    """
    (tmp_path / ".console").mkdir()
    with reconcile_lock(tmp_path):
        raw = (tmp_path / LOCK_RELPATH).read_bytes()
    assert raw[:lock_mod._PID_FIELD].decode().strip() == str(os.getpid())


def test_pid_field_and_lock_byte_do_not_overlap():
    """Layout invariant the Windows backend depends on."""
    assert lock_mod._LOCK_OFFSET >= lock_mod._PID_FIELD


def test_shorter_pid_cannot_leave_a_longer_predecessors_tail(tmp_path):
    """Fixed-width field instead of truncate — truncation would cross the lock."""
    (tmp_path / ".console").mkdir()
    lock_file = tmp_path / LOCK_RELPATH
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_bytes(b"9" * lock_mod._PID_FIELD)  # a long stale pid
    with reconcile_lock(tmp_path):
        pass
    recorded = lock_file.read_bytes()[: lock_mod._PID_FIELD].decode().strip()
    assert recorded == str(os.getpid())
    assert "9" * 8 not in recorded


# The child reports its OWN pid rather than the test trusting `Popen.pid`: a
# venv's python.exe can be a launcher shim, so the process that takes the lock
# is not always the one Popen returns.
_CHILD = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, {src!r})
from context_lifecycle.reconcile.lock import reconcile_lock
with reconcile_lock(Path({repo!r})):
    print("HELD", os.getpid(), flush=True)
    time.sleep(float({hold!r}))
"""


def test_lock_excludes_a_separate_process(tmp_path):
    """The real contract: another *process* is refused while the lock is held.

    The same-process test above passes on any backend that tracks handles; only
    a second process proves the OS is enforcing it.
    """
    (tmp_path / ".console").mkdir()
    src = str(Path(lock_mod.__file__).parents[3])
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD.format(src=src, repo=str(tmp_path), hold="5")],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        held, _, child_pid = child.stdout.readline().strip().partition(" ")
        assert held == "HELD", "child never acquired the lock"
        with pytest.raises(PruneLockHeld) as exc, reconcile_lock(tmp_path):
            pytest.fail("acquired a lock another process holds")
        # The holder's pid is reported, which is the whole point of the layout.
        assert child_pid and child_pid in str(exc.value)
        assert str(os.getpid()) != child_pid, "child must be a separate process"
    finally:
        child.kill()
        child.wait()


def test_lock_is_reacquirable_after_the_holding_process_dies(tmp_path):
    """Both backends release on process death — no stale lock survives a crash."""
    (tmp_path / ".console").mkdir()
    src = str(Path(lock_mod.__file__).parents[3])
    child = subprocess.Popen(
        [sys.executable, "-c", _CHILD.format(src=src, repo=str(tmp_path), hold="30")],
        stdout=subprocess.PIPE, text=True,
    )
    assert child.stdout.readline().split()[0] == "HELD"
    child.kill()
    child.wait()
    acquired = False
    with reconcile_lock(tmp_path):  # stale lock would raise here
        acquired = True
    assert acquired


def test_unexpected_oserror_is_not_reported_as_contention(tmp_path, monkeypatch):
    """A real failure must surface as itself, not as 'someone else holds it'."""
    (tmp_path / ".console").mkdir()

    def _boom(fd):
        raise OSError(errno.EIO, "disk fell over")

    monkeypatch.setattr(lock_mod, "_acquire", _boom)
    with pytest.raises(OSError) as exc, reconcile_lock(tmp_path):
        pytest.fail("must not enter the block")
    assert not isinstance(exc.value, PruneLockHeld)
    assert exc.value.errno == errno.EIO
