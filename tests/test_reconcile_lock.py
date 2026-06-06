# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the per-repo reconcile mutation lock (`reconcile.lock`)."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

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
        with pytest.raises(PruneLockHeld):
            with reconcile_lock(repo):
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
    with reconcile_lock(repo):  # another applier
        with pytest.raises(PruneLockHeld):
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
