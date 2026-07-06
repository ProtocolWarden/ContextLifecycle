# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl loop` CLI surface (PseudoOperator harness)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from context_lifecycle.cli import loop as loop_cli
from context_lifecycle.cli.loop import (
    pause_cmd,
    resume_cmd,
    run_cmd,
    signal_cmd,
    status_cmd,
    stop_cmd,
)

assert all((run_cmd, status_cmd, stop_cmd, pause_cmd, resume_cmd, signal_cmd))

runner = CliRunner()


def _config_file(tmp_path: Path) -> Path:
    (tmp_path / "prompt.txt").write_text("x", encoding="utf-8")
    f = tmp_path / "config.yaml"
    f.write_text(
        "pseudo_operator:\n"
        "  loop_name: t\n"
        f"  repo_root: {tmp_path}\n"
        "  session_prompt_file: prompt.txt\n",
        encoding="utf-8",
    )
    return f


def test_status_no_lock(tmp_path: Path):
    res = runner.invoke(loop_cli.app, ["status", "--config", str(_config_file(tmp_path))])
    assert res.exit_code == 0
    assert "not running" in res.output


def test_stop_and_pause_and_resume_flags(tmp_path: Path):
    cfg_file = _config_file(tmp_path)
    res = runner.invoke(loop_cli.app, ["stop", "--config", str(cfg_file)])
    assert res.exit_code == 0
    assert (tmp_path / "tools/loop/state/stop.flag").exists()

    res = runner.invoke(loop_cli.app, ["pause", "--config", str(cfg_file)])
    assert res.exit_code == 0
    assert (tmp_path / "tools/loop/state/pause.flag").exists()
    status = runner.invoke(loop_cli.app, ["status", "--config", str(cfg_file)])
    assert "PAUSED" in status.output

    res = runner.invoke(loop_cli.app, ["resume", "--config", str(cfg_file)])
    assert res.exit_code == 0
    assert not (tmp_path / "tools/loop/state/pause.flag").exists()


def test_signal_writes_payload(tmp_path: Path):
    cfg_file = _config_file(tmp_path)
    res = runner.invoke(
        loop_cli.app, ["signal", "investigate the thing", "--config", str(cfg_file)]
    )
    assert res.exit_code == 0
    payload = json.loads(
        (tmp_path / "tools/loop/state/signal.json").read_text(encoding="utf-8")
    )
    assert payload["task"] == "investigate the thing"
    assert payload["consumed"] is False


def test_run_refuses_invalid_config(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text("repo: X\n", encoding="utf-8")
    res = runner.invoke(loop_cli.app, ["run", "--config", str(f)])
    assert res.exit_code == 2
    assert "config error" in res.output


def test_run_executes_engine(tmp_path: Path, monkeypatch):
    cfg_file = _config_file(tmp_path)

    class FakeEngine:
        def __init__(self, cfg):
            self.cfg = cfg

        def run(self):
            return 0

    monkeypatch.setattr(loop_cli, "PseudoOperatorEngine", FakeEngine)
    res = runner.invoke(loop_cli.app, ["run", "--config", str(cfg_file)])
    assert res.exit_code == 0
