# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the `cl context` CLI (session-boundary cognition for non-hook CLIs)."""

from __future__ import annotations

import io
import json

from typer.testing import CliRunner

from context_lifecycle.cli import context as ctx
from context_lifecycle.cli.main import app


def test_load_json_literal():
    assert ctx._load_json('{"a": 1}') == {"a": 1}


def test_load_json_from_file(tmp_path):
    p = tmp_path / "wi.json"
    p.write_text('{"repo": "x", "n": 2}', encoding="utf-8")
    assert ctx._load_json(f"@{p}") == {"repo": "x", "n": 2}


def test_load_json_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"from": "stdin"}'))
    assert ctx._load_json("-") == {"from": "stdin"}


def test_context_group_registered_with_subcommands():
    result = CliRunner().invoke(app, ["context", "--help"])
    assert result.exit_code == 0
    for sub in ("hydrate", "capture", "peek"):
        assert sub in result.output


def test_capture_delegates_to_lifecycle(monkeypatch):
    seen = {}
    monkeypatch.setattr(ctx.lifecycle, "capture", lambda lineage, result: seen.update(lineage=lineage, result=result))
    res = CliRunner().invoke(app, ["context", "capture", "--lineage", "L1", "--result", '{"ok": true}'])
    assert res.exit_code == 0
    assert seen == {"lineage": "L1", "result": {"ok": True}}


def test_peek_prints_prior_context(monkeypatch):
    monkeypatch.setattr(ctx.lifecycle, "peek", lambda work_item: {"prior": "capsule"})
    res = CliRunner().invoke(app, ["context", "peek", "--work-item", '{"repo": "x"}'])
    assert res.exit_code == 0
    assert json.loads(res.output) == {"prior": "capsule"}


def test_peek_prints_nothing_when_absent(monkeypatch):
    monkeypatch.setattr(ctx.lifecycle, "peek", lambda work_item: None)
    res = CliRunner().invoke(app, ["context", "peek", "--work-item", "{}"])
    assert res.exit_code == 0
    assert res.output.strip() == ""
