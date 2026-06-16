# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the `cl ledger capture` CLI (context_lifecycle.cli.ledger)."""

from __future__ import annotations

from typer.testing import CliRunner

from context_lifecycle.cli.ledger import app as ledger_app
from context_lifecycle.cli.ledger import capture_cmd
from context_lifecycle.cli.main import app
from context_lifecycle.ledger import LEDGER_SUBPATH

runner = CliRunner()


def test_capture_cmd_is_registered():
    # Direct symbol reference (T1) + the group is wired into the root app (T6).
    assert capture_cmd is not None
    res = runner.invoke(app, ["ledger", "--help"])
    assert res.exit_code == 0
    assert "capture" in res.output


def test_ledger_app_help():
    res = runner.invoke(ledger_app, ["--help"])
    assert res.exit_code == 0


def test_cli_capture_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(tmp_path))
    res = runner.invoke(app, ["ledger", "capture", "pr-closed-by-human", "RepoGraph#42: x"])
    assert res.exit_code == 0
    assert "candidate recorded" in res.output
    assert (tmp_path / LEDGER_SUBPATH).exists()


def test_cli_capture_dedup_message(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(tmp_path))
    runner.invoke(app, ["ledger", "capture", "sig", "ctx"])
    res = runner.invoke(app, ["ledger", "capture", "sig", "ctx"])
    assert res.exit_code == 0
    assert "deduped" in res.output


def test_cli_capture_fail_soft_no_private_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(tmp_path / "does-not-exist"))
    res = runner.invoke(app, ["ledger", "capture", "sig", "ctx"])
    assert res.exit_code == 2
    assert "cl ledger capture:" in res.output
