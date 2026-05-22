from __future__ import annotations

from pathlib import Path

from context_lifecycle.session.paths import SessionPaths, archived_root


def test_session_paths_layout(tmp_path):
    sp = SessionPaths(anchor=tmp_path, session_id="s-2026-05-22-abcd")
    assert sp.root == tmp_path / ".context" / "sessions" / "s-2026-05-22-abcd"
    assert sp.active == sp.root / "active"
    assert sp.checkpoints == sp.root / "checkpoints"
    assert sp.handoffs == sp.root / "handoffs"
    assert sp.config_file == tmp_path / ".context" / "config.yaml"
    assert sp.archived_target == tmp_path / ".context" / "archived" / "s-2026-05-22-abcd"


def test_session_paths_ensure(tmp_path):
    sp = SessionPaths(anchor=tmp_path, session_id="s-x")
    sp.ensure()
    assert sp.active.is_dir()
    assert sp.checkpoints.is_dir()
    assert sp.handoffs.is_dir()


def test_archived_root(tmp_path):
    assert archived_root(tmp_path) == tmp_path / ".context" / "archived"
