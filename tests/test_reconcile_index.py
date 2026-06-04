# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for `cl reconcile index` — the generated dashboard (spec §3.4 / AC11)."""

from __future__ import annotations

from pathlib import Path

from context_lifecycle.reconcile.index import (
    build_index,
    load_public_repo_names,
    render_index,
)


def _make_repo(clones: Path, name: str, items_yaml: str, doc_paths=()) -> None:
    repo = clones / name
    console = repo / ".console"
    console.mkdir(parents=True)
    for d in doc_paths:
        p = repo / d
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x", encoding="utf-8")
    (console / "reconcile.yaml").write_text(f"repo: {name}\nitems:\n{items_yaml}", encoding="utf-8")


def _manifest(tmp_path: Path) -> Path:
    m = tmp_path / "platform_manifest.yaml"
    m.write_text(
        "repos:\n"
        "  pubone:\n    canonical_name: PubOne\n    visibility: public\n"
        "  pubtwo:\n    canonical_name: PubTwo\n    visibility: public\n",
        encoding="utf-8",
    )
    return m


def test_ac11_private_repos_opaque_public_itemized(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    clones = tmp_path / "clones"
    clones.mkdir()
    _make_repo(
        clones,
        "PubOne",
        "  - id: a\n    status: done\n    doc: [docs/a.md]\n  - id: b\n    status: partial\n",
        doc_paths=["docs/a.md"],
    )
    _make_repo(clones, "PubTwo", "  - id: c\n    status: incomplete\n")
    # Two private repos (not in the manifest) — must collapse to a count.
    _make_repo(clones, "PrivAlpha", "  - id: x\n    status: done\n    doc: []\n")
    _make_repo(clones, "PrivBeta", "  - id: y\n    status: done\n    doc: []\n")

    public = load_public_repo_names(_manifest(tmp_path))
    result = build_index(clones, public)
    rendered = render_index(result)

    assert {r.repo for r in result.public_rows} == {"PubOne", "PubTwo"}
    assert result.private_count == 2
    # Private repo names never appear.
    assert "PrivAlpha" not in rendered
    assert "PrivBeta" not in rendered
    assert "2 private repo(s)" in rendered
    # Public detail present.
    assert "PubOne" in rendered and "PubTwo" in rendered


def test_public_row_counts_and_prune_ready(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    clones = tmp_path / "clones"
    clones.mkdir()
    _make_repo(
        clones,
        "PubOne",
        "  - id: a\n    status: done\n    doc: [docs/a.md]\n",
        doc_paths=["docs/a.md"],
    )
    public = load_public_repo_names(_manifest(tmp_path))
    result = build_index(clones, public)
    row = next(r for r in result.public_rows if r.repo == "PubOne")
    assert row.done == 1
    assert row.doc_gaps == 0
    assert row.prune_ready is True


def test_load_public_repo_names(tmp_path):
    names = load_public_repo_names(_manifest(tmp_path))
    assert names == {"PubOne", "PubTwo"}
