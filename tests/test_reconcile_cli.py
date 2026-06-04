# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the `cl reconcile` CLI wiring (spec §3)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from context_lifecycle.cli.main import app


def _ws(repo: Path, text: str) -> None:
    console = repo / ".console"
    console.mkdir(parents=True, exist_ok=True)
    (console / "reconcile.yaml").write_text(text, encoding="utf-8")


def test_reconcile_group_registered():
    res = CliRunner().invoke(app, ["reconcile", "--help"])
    assert res.exit_code == 0
    for sub in ("check", "prune", "index"):
        assert sub in res.output


def test_check_exit_nonzero_on_gap(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    _ws(tmp_path, "repo: R\nitems:\n  - id: gap\n    status: done\n    doc: []\n")
    res = CliRunner().invoke(app, ["reconcile", "check", "--repo", str(tmp_path)])
    assert res.exit_code == 1
    assert "DOC GAP" in res.output


def test_check_exit_zero_when_green(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    (tmp_path / "d.md").write_text("x", encoding="utf-8")
    _ws(tmp_path, "repo: R\nitems:\n  - id: ok\n    status: done\n    doc: [d.md]\n")
    res = CliRunner().invoke(app, ["reconcile", "check", "--repo", str(tmp_path)])
    assert res.exit_code == 0
    assert "GREEN" in res.output


def test_prune_dry_run_default(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(tmp_path / "priv"))
    (tmp_path / "priv").mkdir()
    repo = tmp_path / "R"
    console = repo / ".console"
    console.mkdir(parents=True)
    (repo / "d.md").write_text("x", encoding="utf-8")
    _ws(repo, "repo: R\nitems:\n  - id: feature-x\n    title: 'Feature X'\n    status: done\n    owner: R\n    doc: [d.md]\n")
    (console / "log.md").write_text("# Log\n\n## 2026-05-30 — Feature X landed\n\nbody\n\n", encoding="utf-8")
    res = CliRunner().invoke(app, ["reconcile", "prune", "--repo", str(repo)])
    assert res.exit_code == 0
    assert "dry-run" in res.output
    # nothing archived
    assert not (tmp_path / "priv" / "archive").exists()


def test_index_writes_file(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    clones = tmp_path / "clones"
    repo = clones / "PubOne"
    (repo / ".console").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "a.md").write_text("x", encoding="utf-8")
    (repo / ".console" / "reconcile.yaml").write_text(
        "repo: PubOne\nitems:\n  - id: a\n    status: done\n    doc: [docs/a.md]\n", encoding="utf-8"
    )
    manifest = tmp_path / "platform_manifest.yaml"
    manifest.write_text(
        "repos:\n  pubone:\n    canonical_name: PubOne\n    visibility: public\n", encoding="utf-8"
    )
    out = tmp_path / "status.md"
    res = CliRunner().invoke(
        app,
        ["reconcile", "index", "--clones-root", str(clones), "--manifest", str(manifest), "--out", str(out)],
    )
    assert res.exit_code == 0
    assert out.is_file()
    assert "PubOne" in out.read_text(encoding="utf-8")
