from __future__ import annotations

import json

from typer.testing import CliRunner

from context_lifecycle.cli.main import app

runner = CliRunner()


def test_hook_pre_tool_use_no_anchor(monkeypatch):
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    monkeypatch.delenv("CL_SESSION_ID", raising=False)
    result = runner.invoke(app, ["hook", "pre_tool_use"], input='{"tool_name":"Read"}')
    assert result.exit_code == 2
    assert "CL_ANCHOR" in (result.stderr or result.output or "")


def test_hook_pre_tool_use_allows(monkeypatch, tmp_path):
    sid = "s-2026-05-22-abcd"
    (tmp_path / ".context" / "sessions" / sid / "active").mkdir(parents=True)
    (tmp_path / ".context" / "sessions" / sid / "checkpoints").mkdir(parents=True)
    (tmp_path / ".context" / "sessions" / sid / "handoffs").mkdir(parents=True)
    monkeypatch.setenv("CL_ANCHOR", str(tmp_path))
    monkeypatch.setenv("CL_SESSION_ID", sid)
    result = runner.invoke(app, ["hook", "pre_tool_use"], input='{"tool_name":"Read","tool_input":{}}')
    assert result.exit_code == 0


def test_hook_pre_tool_use_blocks(monkeypatch, tmp_path):
    import yaml as _yaml

    sid = "s-2026-05-22-abcd"
    sroot = tmp_path / ".context" / "sessions" / sid
    (sroot / "active").mkdir(parents=True)
    (sroot / "checkpoints").mkdir(parents=True)
    handoffs = sroot / "handoffs"
    handoffs.mkdir(parents=True)
    with open(handoffs / "h.yaml", "w") as f:
        _yaml.safe_dump(
            {
                "worker_scope": {
                    "mutation_policy": "read_only",
                    "allowed_paths": [],
                    "forbidden_paths": [],
                },
                "lease": {"max_subagents": 1},
            },
            f,
        )
    monkeypatch.setenv("CL_ANCHOR", str(tmp_path))
    monkeypatch.setenv("CL_SESSION_ID", sid)
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "src/x.py"}})
    result = runner.invoke(app, ["hook", "pre_tool_use"], input=payload)
    assert result.exit_code == 2
    assert "read_only" in (result.stderr or result.output or "")


def test_hook_stop_no_anchor(monkeypatch):
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    monkeypatch.delenv("CL_SESSION_ID", raising=False)
    result = runner.invoke(app, ["hook", "stop"], input="{}")
    assert result.exit_code == 2


def test_hook_stop_runs(monkeypatch, tmp_path):
    sid = "s-2026-05-22-abcd"
    sroot = tmp_path / ".context" / "sessions" / sid
    (sroot / "active").mkdir(parents=True)
    (sroot / "checkpoints").mkdir(parents=True)
    (sroot / "handoffs").mkdir(parents=True)
    monkeypatch.setenv("CL_ANCHOR", str(tmp_path))
    monkeypatch.setenv("CL_SESSION_ID", sid)
    result = runner.invoke(app, ["hook", "stop"], input="{}")
    # stop is structurally non-fatal
    assert result.exit_code == 0
