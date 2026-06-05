# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for `cl reconcile check` — the I1 gate (spec §3.2 / AC5,AC6,AC7)."""

from __future__ import annotations

from pathlib import Path

from context_lifecycle.reconcile.check import run_check
from context_lifecycle.reconcile.scrub import load_scrub_vocabulary

SCRUB_NAME = "SecretRepo"  # synthetic scrub target (keeps test file boundary-clean)


def _ws(repo_root: Path, text: str) -> None:
    console = repo_root / ".console"
    console.mkdir(parents=True, exist_ok=True)
    (console / "reconcile.yaml").write_text(text, encoding="utf-8")


# --- AC5: done + empty doc fails; filling doc flips to green -----------------


def test_ac5_done_empty_doc_fails_named(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    _ws(tmp_path, "repo: R\nitems:\n  - id: alpha\n    title: Alpha\n    status: done\n    doc: []\n")
    result = run_check(tmp_path)
    assert not result.green
    assert any(g.item_id == "alpha" for g in result.doc_gaps)


def test_ac5_filling_existing_doc_flips_green(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("design", encoding="utf-8")
    _ws(tmp_path, "repo: R\nitems:\n  - id: alpha\n    status: done\n    doc: [docs/a.md]\n")
    result = run_check(tmp_path)
    assert result.green
    assert result.doc_gaps == []


def test_done_doc_path_missing_is_gap(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    _ws(tmp_path, "repo: R\nitems:\n  - id: alpha\n    status: done\n    doc: [docs/nope.md]\n")
    result = run_check(tmp_path)
    assert not result.green
    assert any("missing:docs/nope.md" == g.reason for g in result.doc_gaps)


# --- AC6: scrub-target name in any field fails check ------------------------


def test_ac6_scrub_name_in_field_fails(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    vocab = load_scrub_vocabulary(extra_names=[SCRUB_NAME])
    (tmp_path / "d.md").write_text("x", encoding="utf-8")
    _ws(
        tmp_path,
        f"repo: R\nitems:\n  - id: alpha\n    title: 'work for {SCRUB_NAME}'\n    status: done\n    doc: [d.md]\n",
    )
    result = run_check(tmp_path, vocab=vocab)
    assert not result.green
    assert result.scrub_hits
    assert result.scrub_hits[0].field == "title"


# --- AC7: cross-repo items listed, not gated -------------------------------


def test_ac7_cross_repo_listed_not_gated(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    # A cross-repo done item with empty doc must NOT block (it's routed, not gated).
    _ws(
        tmp_path,
        "repo: R\nitems:\n  - id: beta\n    status: done\n    owner: OtherRepo\n    doc: []\n",
    )
    result = run_check(tmp_path)
    assert result.green
    assert [it.id for it in result.cross_repo] == ["beta"]
    assert result.doc_gaps == []


# --- Private repo reconciling itself: scrub gate skipped --------------------


def test_private_repo_self_name_skips_scrub_gate(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    vocab = load_scrub_vocabulary(extra_names=[SCRUB_NAME])
    (tmp_path / "d.md").write_text("x", encoding="utf-8")
    # The repo's own name IS the scrub target — a private repo's worksheet.
    _ws(
        tmp_path,
        f"repo: {SCRUB_NAME}\n"
        "items:\n"
        f"  - id: alpha\n    title: 'work inside {SCRUB_NAME}'\n"
        f"    status: done\n    owner: {SCRUB_NAME}\n    doc: [d.md]\n",
    )
    result = run_check(tmp_path, vocab=vocab)
    assert result.green
    assert result.scrub_hits == []
    assert any("private repo" in w for w in result.warnings)


def test_private_repo_doc_gap_still_gates(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    vocab = load_scrub_vocabulary(extra_names=[SCRUB_NAME])
    _ws(
        tmp_path,
        f"repo: {SCRUB_NAME}\n"
        "items:\n"
        f"  - id: alpha\n    status: done\n    owner: {SCRUB_NAME}\n    doc: []\n",
    )
    result = run_check(tmp_path, vocab=vocab)
    assert not result.green
    assert any(g.item_id == "alpha" for g in result.doc_gaps)


def test_private_manifest_root_skips_scrub_gate(tmp_path, monkeypatch):
    """The private-manifest repo itself: name NOT in vocab, detected by root."""
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(tmp_path))
    vocab = load_scrub_vocabulary(extra_names=[SCRUB_NAME])
    (tmp_path / "d.md").write_text("x", encoding="utf-8")
    _ws(
        tmp_path,
        "repo: ManifestHost\n"
        "items:\n"
        f"  - id: alpha\n    title: 'registry rename for {SCRUB_NAME}'\n"
        "    status: done\n    owner: ManifestHost\n    doc: [d.md]\n",
    )
    result = run_check(tmp_path, vocab=vocab)
    assert result.green
    assert result.scrub_hits == []
    assert any("private repo" in w for w in result.warnings)
