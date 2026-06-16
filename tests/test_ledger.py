# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the `cl ledger capture` CLI (context_lifecycle.cli.ledger)."""

from __future__ import annotations

from typer.testing import CliRunner

from context_lifecycle.cli.ledger import app as ledger_app
from context_lifecycle.cli.ledger import capture_cmd, check_cmd
from context_lifecycle.cli.main import app
from context_lifecycle.ledger import LEDGER_SUBPATH

runner = CliRunner()


def test_capture_cmd_is_registered():
    # Direct symbol reference (T1) + the group is wired into the root app (T6).
    assert capture_cmd is not None
    assert check_cmd is not None
    res = runner.invoke(app, ["ledger", "--help"])
    assert res.exit_code == 0
    assert "capture" in res.output
    assert "check" in res.output


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


def test_cli_check_clean_exit_zero(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(tmp_path))
    res = runner.invoke(app, ["ledger", "check"])
    assert res.exit_code == 0
    assert "OK" in res.output


def test_cli_check_stale_exit_one(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(tmp_path))
    # An ancient candidate (year 2000) is always past any sane threshold.
    runner.invoke(app, ["ledger", "capture", "sig", "ctx", "--date", "2000-01-01"])
    res = runner.invoke(app, ["ledger", "check"])
    assert res.exit_code == 1
    assert "unpromoted candidate" in res.output


def test_cli_check_fail_soft_no_private_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(tmp_path / "nope"))
    res = runner.invoke(app, ["ledger", "check"])
    assert res.exit_code == 0  # fail-soft: nothing to check
    assert "no ledger to check" in res.output
