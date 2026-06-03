# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

import json

from typer.testing import CliRunner

from context_lifecycle.cli import session as session_cli
from context_lifecycle.cli.main import app

runner = CliRunner()


def test_session_commands_registered():
    # Direct reference to the cli.session module symbols (import-coverage + T1).
    assert callable(session_cli.start)
    assert callable(session_cli.show)
    assert callable(session_cli.end)
    assert session_cli.app is not None


def test_session_start_no_arg_errors_when_unresolvable(tmp_path, monkeypatch):
    # Point RepoGraph at an empty registry so cwd inference returns None.
    monkeypatch.setenv("REPOGRAPH_REGISTRY", str(tmp_path / "empty-registry.yaml"))
    result = runner.invoke(app, ["session", "start"])
    assert result.exit_code == 1
    out = (result.stderr or "") + result.output
    assert "could not infer" in out or "not registered" in out or "RepoGraph" in out


def test_session_start_bad_path():
    result = runner.invoke(app, ["session", "start", "/nope/missing"])
    assert result.exit_code == 1


def test_session_start_emits_exports(tmp_path):
    result = runner.invoke(app, ["session", "start", str(tmp_path)])
    assert result.exit_code == 0
    out = result.stdout
    assert "export CL_ANCHOR=" in out
    assert str(tmp_path) in out
    assert "export CL_SESSION_ID=" in out
    assert "s-" in out


def test_session_start_json(tmp_path):
    result = runner.invoke(app, ["session", "start", "--json", str(tmp_path)])
    assert result.exit_code == 0
    data = json.loads(result.stdout.strip())
    assert data["anchor"] == str(tmp_path.resolve())
    assert data["session_id"].startswith("s-")
    assert data["manifest_name"] == tmp_path.name


def test_session_start_json_and_shell_mutex(tmp_path):
    result = runner.invoke(app, ["session", "start", "--json", "--shell", str(tmp_path)])
    assert result.exit_code == 1


def test_session_show_unset(monkeypatch):
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    monkeypatch.delenv("CL_SESSION_ID", raising=False)
    result = runner.invoke(app, ["session", "show"])
    assert result.exit_code == 1


def test_session_show_set(monkeypatch, tmp_path):
    monkeypatch.setenv("CL_ANCHOR", str(tmp_path))
    monkeypatch.setenv("CL_SESSION_ID", "s-2026-05-22-abcd")
    result = runner.invoke(app, ["session", "show"])
    assert result.exit_code == 0
    assert str(tmp_path) in result.stdout
    assert "s-2026-05-22-abcd" in result.stdout


def test_session_end_emits_unset():
    result = runner.invoke(app, ["session", "end", "--no-archive"])
    assert result.exit_code == 0
    assert "unset CL_ANCHOR CL_SESSION_ID" in result.stdout


def test_session_end_archives(monkeypatch, tmp_path):
    sid = "s-2026-05-22-abcd"
    anchor = tmp_path
    sessions_root = anchor / ".context" / "sessions" / sid
    (sessions_root / "active").mkdir(parents=True)
    (sessions_root / "checkpoints").mkdir(parents=True)
    monkeypatch.setenv("CL_ANCHOR", str(anchor))
    monkeypatch.setenv("CL_SESSION_ID", sid)
    result = runner.invoke(app, ["session", "end"])
    assert result.exit_code == 0
    assert not sessions_root.exists()
    assert (anchor / ".context" / "archived" / sid).is_dir()
