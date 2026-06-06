# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for session retention (`cl session prune` / session.retention)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from context_lifecycle.cli.main import app
from context_lifecycle.session.retention import (
    PruneCandidate,
    SessionPrunePlan,
    apply_session_prune,
    build_session_prune_plan,
    format_session_prune_plan,
)

TODAY = date(2026, 6, 6)


def _mk_session(anchor: Path, sid: str, *, archived: bool = False, leases: int = 3) -> Path:
    root = anchor / ".context" / ("archived" if archived else "sessions") / sid
    active = root / "active"
    active.mkdir(parents=True)
    for i in range(leases):
        (active / f"l-{i}.yaml").write_text(f"run_id: run-{i}\n", encoding="utf-8")
    return root


def _anchor(tmp_path: Path) -> Path:
    (tmp_path / ".context").mkdir()
    return tmp_path


# --- plan ------------------------------------------------------------------


def test_old_sessions_planned_young_kept(tmp_path: Path):
    anchor = _anchor(tmp_path)
    old = _mk_session(anchor, "s-2026-05-01-aaaa")
    _mk_session(anchor, "s-2026-06-05-bbbb")  # 1 day old — kept
    plan = build_session_prune_plan(anchor, retain_days=14, today=TODAY)
    assert isinstance(plan, SessionPrunePlan)
    assert all(isinstance(c, PruneCandidate) for c in plan.candidates)
    assert [c.session_id for c in plan.candidates] == ["s-2026-05-01-aaaa"]
    assert "s-2026-06-05-bbbb" in plan.kept
    assert plan.file_count == 3
    assert old.is_dir()  # planning never mutates


def test_current_session_always_kept(tmp_path: Path):
    anchor = _anchor(tmp_path)
    _mk_session(anchor, "s-2026-01-01-cccc")  # ancient but current
    plan = build_session_prune_plan(
        anchor, retain_days=14, today=TODAY, current_session_id="s-2026-01-01-cccc"
    )
    assert plan.candidates == []
    assert plan.kept == ["s-2026-01-01-cccc"]


def test_undatable_dir_kept(tmp_path: Path):
    anchor = _anchor(tmp_path)
    weird = anchor / ".context" / "sessions" / "not-a-session-id"
    weird.mkdir(parents=True)
    # mtime is "now" → younger than cutoff → kept via fallback dating.
    plan = build_session_prune_plan(anchor, retain_days=14, today=TODAY)
    assert plan.candidates == []
    assert "not-a-session-id" in plan.kept


def test_archived_pruned_only_with_flag(tmp_path: Path):
    anchor = _anchor(tmp_path)
    _mk_session(anchor, "s-2026-05-01-dddd", archived=True)
    plan = build_session_prune_plan(anchor, retain_days=14, today=TODAY)
    assert plan.candidates == []
    plan2 = build_session_prune_plan(
        anchor, retain_days=14, today=TODAY, include_archived=True
    )
    assert [c.session_id for c in plan2.candidates] == ["s-2026-05-01-dddd"]


def test_boundary_is_strictly_older_than_cutoff(tmp_path: Path):
    anchor = _anchor(tmp_path)
    _mk_session(anchor, "s-2026-05-23-eeee")  # exactly cutoff (14 days) — kept
    _mk_session(anchor, "s-2026-05-22-ffff")  # one day past — pruned
    plan = build_session_prune_plan(anchor, retain_days=14, today=TODAY)
    assert [c.session_id for c in plan.candidates] == ["s-2026-05-22-ffff"]
    assert "s-2026-05-23-eeee" in plan.kept


# --- apply -----------------------------------------------------------------


def test_apply_deletes_candidates_and_is_idempotent(tmp_path: Path):
    anchor = _anchor(tmp_path)
    old = _mk_session(anchor, "s-2026-05-01-aaaa")
    young = _mk_session(anchor, "s-2026-06-05-bbbb")
    plan = build_session_prune_plan(anchor, retain_days=14, today=TODAY)
    apply_session_prune(plan)
    assert plan.applied
    assert not old.exists()
    assert young.is_dir()
    # Re-apply over the same plan is a no-op, not an error.
    apply_session_prune(plan)
    plan2 = build_session_prune_plan(anchor, retain_days=14, today=TODAY)
    assert plan2.candidates == []


def test_format_lists_sessions_and_totals(tmp_path: Path):
    anchor = _anchor(tmp_path)
    _mk_session(anchor, "s-2026-05-01-aaaa")
    plan = build_session_prune_plan(anchor, retain_days=14, today=TODAY)
    out = format_session_prune_plan(plan, applied=False)
    assert "dry-run" in out
    assert "s-2026-05-01-aaaa" in out
    assert "1 session(s), 3 file(s)" in out


# --- CLI -------------------------------------------------------------------


def test_cli_dry_run_default_and_apply(tmp_path: Path, monkeypatch):
    anchor = _anchor(tmp_path)
    old = _mk_session(anchor, "s-2026-05-01-aaaa")
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    monkeypatch.delenv("CL_SESSION_ID", raising=False)

    res = CliRunner().invoke(app, ["session", "prune", str(anchor)])
    assert res.exit_code == 0
    assert "dry-run" in res.output
    assert old.is_dir()

    res = CliRunner().invoke(app, ["session", "prune", str(anchor), "--apply"])
    assert res.exit_code == 0
    assert "applied" in res.output
    assert not old.exists()


def test_cli_defaults_to_env_anchor(tmp_path: Path, monkeypatch):
    anchor = _anchor(tmp_path)
    _mk_session(anchor, "s-2026-05-01-aaaa")
    monkeypatch.setenv("CL_ANCHOR", str(anchor))
    monkeypatch.delenv("CL_SESSION_ID", raising=False)
    res = CliRunner().invoke(app, ["session", "prune"])
    assert res.exit_code == 0
    assert "s-2026-05-01-aaaa" in res.output


def test_cli_errors_without_anchor(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    res = CliRunner().invoke(app, ["session", "prune"])
    assert res.exit_code == 1


def test_cli_requires_context_dir(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    res = CliRunner().invoke(app, ["session", "prune", str(tmp_path)])
    assert res.exit_code == 3
