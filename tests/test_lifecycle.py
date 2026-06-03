# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the public lifecycle API (ADR 0002 P4)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import context_lifecycle.lifecycle as lifecycle_module
from context_lifecycle import (
    AnchorMissing,
    BoundaryViolation,
    HydratedContext,
    SessionNotStarted,
    capture,
    hydrate,
    peek,
)
from context_lifecycle.session.paths import SessionPaths


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _AllowAllGraph:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, str]] = []

    def can_anchor_host(self, anchor: Path, repo: str) -> tuple[bool, str]:
        self.calls.append((anchor, repo))
        return True, f"stub-allow {repo}"


class _DenyGraph:
    def __init__(self, deny_repo: str) -> None:
        self.deny_repo = deny_repo
        self.calls: list[tuple[Path, str]] = []

    def can_anchor_host(self, anchor: Path, repo: str) -> tuple[bool, str]:
        self.calls.append((anchor, repo))
        if repo == self.deny_repo:
            return False, f"deny via stub: {repo!r} not authorized"
        return True, "ok"


@pytest.fixture
def session_env(monkeypatch, anchor: Path, session_id: str):
    monkeypatch.setenv("CL_ANCHOR", str(anchor))
    monkeypatch.setenv("CL_SESSION_ID", session_id)
    SessionPaths(anchor=anchor, session_id=session_id).ensure()
    return anchor, session_id


@pytest.fixture
def patch_repograph(monkeypatch):
    """Install a fake `repograph` module so _enforce_boundary uses our stub."""

    def install(graph):
        import sys
        import types

        fake = types.ModuleType("repograph")
        fake.RepoGraph = lambda: graph  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "repograph", fake)
        return graph

    return install


# ---------------------------------------------------------------------------
# hydrate
# ---------------------------------------------------------------------------


def test_hydrate_fresh_returns_initialized_context(session_env) -> None:
    anchor, sid = session_env
    ctx = hydrate("l-team-001", {"task": "demo"})

    assert isinstance(ctx, HydratedContext)
    assert ctx.lineage_id == "l-team-001"
    assert ctx.session_id == sid
    assert ctx.anchor_path == anchor
    assert ctx.fresh is True
    assert ctx.capsule["status"] == "fresh"
    assert ctx.capsule["lineage_id"] == "l-team-001"
    assert ctx.capsule["work_item"] == {"task": "demo"}
    assert ctx.latest_checkpoint is None
    assert ctx.active_handoff is None
    # Hydrate must NOT write to disk.
    assert not (anchor / ".context" / "sessions" / sid / "active" / "l-team-001.yaml").exists()


def test_hydrate_resume_returns_existing_capsule(session_env) -> None:
    anchor, sid = session_env
    active_file = anchor / ".context" / "sessions" / sid / "active" / "l-team-001.yaml"
    active_file.parent.mkdir(parents=True, exist_ok=True)
    with active_file.open("w") as f:
        yaml.safe_dump({"lineage_id": "l-team-001", "status": "in_progress", "notes": "n"}, f)

    ctx = hydrate("l-team-001", {"task": "demo"})

    assert ctx.fresh is False
    assert ctx.capsule["status"] == "in_progress"
    assert ctx.capsule["notes"] == "n"


def test_hydrate_loads_latest_checkpoint(session_env) -> None:
    anchor, sid = session_env
    ckpt_dir = anchor / ".context" / "sessions" / sid / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in [
        ("2026-05-22T10-00-00Z.yaml", {"checkpoint_id": "older"}),
        ("2026-05-22T11-00-00Z.yaml", {"checkpoint_id": "newer"}),
    ]:
        with (ckpt_dir / name).open("w") as f:
            yaml.safe_dump(payload, f)

    ctx = hydrate("l-team-001", {})
    assert ctx.latest_checkpoint == {"checkpoint_id": "newer"}


def test_hydrate_raises_when_anchor_unset(monkeypatch) -> None:
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    monkeypatch.setenv("CL_SESSION_ID", "s-2026-05-22-a1b2")
    with pytest.raises(AnchorMissing):
        hydrate("l-1", {})


def test_hydrate_raises_when_session_unset(monkeypatch, anchor: Path) -> None:
    monkeypatch.setenv("CL_ANCHOR", str(anchor))
    monkeypatch.delenv("CL_SESSION_ID", raising=False)
    with pytest.raises(SessionNotStarted):
        hydrate("l-1", {})


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def test_capture_writes_capsule_to_active(session_env, patch_repograph) -> None:
    anchor, sid = session_env
    patch_repograph(_AllowAllGraph())

    capture("l-team-001", {"status": "in_progress", "notes": "step done"})

    out = anchor / ".context" / "sessions" / sid / "active" / "l-team-001.yaml"
    assert out.exists()
    data = yaml.safe_load(out.read_text())
    assert data["status"] == "in_progress"
    assert data["lineage_id"] == "l-team-001"
    assert data["session_id"] == sid


def test_capture_writes_checkpoint(session_env, patch_repograph) -> None:
    anchor, sid = session_env
    patch_repograph(_AllowAllGraph())

    capture("l-team-001", {"checkpoint_id": "ckpt-001", "orchestrator": {}})

    out = anchor / ".context" / "sessions" / sid / "checkpoints" / "ckpt-001.yaml"
    assert out.exists()


def test_capture_writes_handoff(session_env, patch_repograph) -> None:
    anchor, sid = session_env
    patch_repograph(_AllowAllGraph())

    capture(
        "l-team-001",
        {
            "handoff_id": "worker-1",
            "worker_scope": {"repo": "TestRepo"},
        },
    )

    out = anchor / ".context" / "sessions" / sid / "handoffs" / "worker-1.yaml"
    assert out.exists()


def test_capture_calls_repograph_for_each_repo(session_env, patch_repograph) -> None:
    anchor, sid = session_env
    graph = patch_repograph(_AllowAllGraph())

    capture(
        "l-team-001",
        {"repo": "RepoA", "targets": ["RepoB", "RepoC"]},
    )

    seen_repos = [repo for _, repo in graph.calls]
    assert set(seen_repos) == {"RepoA", "RepoB", "RepoC"}
    assert all(a == anchor for a, _ in graph.calls)


def test_capture_boundary_violation_aborts_write(session_env, patch_repograph) -> None:
    anchor, sid = session_env
    patch_repograph(_DenyGraph(deny_repo="RepoB"))

    with pytest.raises(BoundaryViolation):
        capture("l-team-001", {"repos": ["RepoA", "RepoB"], "status": "x"})

    out = anchor / ".context" / "sessions" / sid / "active" / "l-team-001.yaml"
    assert not out.exists(), "capture must not write when boundary check fails"


def test_capture_with_no_repos_skips_repograph(session_env, monkeypatch) -> None:
    anchor, sid = session_env
    # Make repograph import explode if reached; ensures we never call it.
    import sys

    monkeypatch.setitem(sys.modules, "repograph", None)

    capture("l-team-001", {"status": "in_progress"})
    out = anchor / ".context" / "sessions" / sid / "active" / "l-team-001.yaml"
    assert out.exists()


def test_capture_raises_when_anchor_unset(monkeypatch) -> None:
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    monkeypatch.setenv("CL_SESSION_ID", "s-2026-05-22-a1b2")
    with pytest.raises(AnchorMissing):
        capture("l-1", {"status": "x"})


# ---------------------------------------------------------------------------
# peek
# ---------------------------------------------------------------------------


def test_peek_returns_existing_capsule(session_env) -> None:
    anchor, sid = session_env
    active = anchor / ".context" / "sessions" / sid / "active" / "l-team-001.yaml"
    active.parent.mkdir(parents=True, exist_ok=True)
    with active.open("w") as f:
        yaml.safe_dump({"lineage_id": "l-team-001", "status": "in_progress"}, f)

    got = peek({"lineage_id": "l-team-001"})
    assert got is not None
    assert got["status"] == "in_progress"


def test_peek_returns_none_when_no_capsule(session_env) -> None:
    assert peek({"lineage_id": "l-does-not-exist"}) is None


def test_peek_returns_none_when_work_item_lacks_lineage(session_env) -> None:
    assert peek({"task": "no lineage here"}) is None


def test_peek_never_writes(session_env) -> None:
    anchor, sid = session_env
    peek({"lineage_id": "l-team-001"})
    active_dir = anchor / ".context" / "sessions" / sid / "active"
    # Only the directory exists, no files.
    assert list(active_dir.iterdir()) == []


def test_peek_raises_when_anchor_unset(monkeypatch) -> None:
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    monkeypatch.setenv("CL_SESSION_ID", "s-2026-05-22-a1b2")
    with pytest.raises(AnchorMissing):
        peek({"lineage_id": "l-1"})


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_public_api_exposed() -> None:
    """Smoke: dispatcher integration imports must succeed."""
    from context_lifecycle import HydratedContext, capture, hydrate, peek

    assert callable(hydrate)
    assert callable(capture)
    assert callable(peek)
    assert HydratedContext is lifecycle_module.HydratedContext
