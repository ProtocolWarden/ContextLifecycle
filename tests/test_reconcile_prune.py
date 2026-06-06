# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for `cl reconcile prune` — move-and-trim (spec §3.3 / AC8,AC9,AC10)."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from context_lifecycle.reconcile.prune import (
    PruneRefused,
    apply_plan,
    build_plan,
)
from context_lifecycle.reconcile.scrub import load_scrub_vocabulary

SCRUB_NAME = "SecretRepo"


def _checksum(p: Path) -> str:
    return sha256(p.read_bytes()).hexdigest()


def _setup_repo(tmp_path, *, scrub_in_log=False) -> Path:
    repo = tmp_path / "PilotRepo"
    console = repo / ".console"
    console.mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("design doc", encoding="utf-8")
    (console / "reconcile.yaml").write_text(
        "repo: PilotRepo\n"
        "items:\n"
        "  - id: detectors-trio\n"
        "    title: 'Detectors trio'\n"
        "    status: done\n"
        "    owner: PilotRepo\n"
        "    doc: [docs/design.md]\n",
        encoding="utf-8",
    )
    leak = f" owned by {SCRUB_NAME}" if scrub_in_log else ""
    log = (
        "# Log\n\n"
        "## 2026-05-30 — detectors trio shipped\n\n"
        f"Completed the detectors work.{leak}\n\n"
    )
    # Add many recent unrelated entries to exercise recent-N trimming.
    for i in range(12):
        log += f"## 2026-05-{10 + i:02d} — misc entry {i}\n\nNarration {i}.\n\n"
    (console / "log.md").write_text(log, encoding="utf-8")
    (console / "backlog.md").write_text(
        "# Backlog\n\n## In Progress\n\n- [ ] active thing\n\n"
        "## Done\n\n- [x] detectors trio\n\n",
        encoding="utf-8",
    )
    return repo


# --- AC8: dry-run reports moves, mutates nothing ---------------------------


def test_ac8_dry_run_mutates_nothing(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    repo = _setup_repo(tmp_path)
    private = tmp_path / "PrivateSide"
    log = repo / ".console" / "log.md"
    backlog = repo / ".console" / "backlog.md"
    before = (_checksum(log), _checksum(backlog))

    plan = build_plan(repo, private_root=private)
    assert plan.moves  # planned something
    assert any(m.source == "log.md" for m in plan.moves)

    after = (_checksum(log), _checksum(backlog))
    assert before == after  # nothing written
    assert not private.exists()


# --- AC9: --apply moves history, leaves active+recent, pointer+CHANGELOG, idempotent


def test_ac9_apply_moves_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    repo = _setup_repo(tmp_path)
    private = tmp_path / "PrivateSide"

    plan = build_plan(repo, recent_n=10, private_root=private)
    apply_plan(repo, plan, recent_n=10)

    archive_files = list((private / "archive" / "console" / "PilotRepo").glob("log-*.md"))
    assert archive_files, "archive log file created on private side"
    archived_text = archive_files[0].read_text(encoding="utf-8")
    assert "detectors trio shipped" in archived_text

    log_text = (repo / ".console" / "log.md").read_text(encoding="utf-8")
    assert "detectors trio shipped" not in log_text  # claimed section moved
    assert "_Archived completed history →" in log_text  # pointer present
    assert "misc entry 0" in log_text  # a recent entry retained

    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "detectors-trio" in changelog

    # Idempotent: a second apply over a fresh green plan is a no-op.
    log_before = _checksum(repo / ".console" / "log.md")
    cl_before = _checksum(repo / "CHANGELOG.md")
    plan2 = build_plan(repo, recent_n=10, private_root=private)
    apply_plan(repo, plan2, recent_n=10)
    assert _checksum(repo / ".console" / "log.md") == log_before
    assert _checksum(repo / "CHANGELOG.md") == cl_before


# --- AC10: after prune, check still green + no scrub names in tracked source


def test_ac10_post_prune_check_green_and_clean(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    repo = _setup_repo(tmp_path)
    private = tmp_path / "PrivateSide"
    plan = build_plan(repo, recent_n=10, private_root=private)
    apply_plan(repo, plan, recent_n=10)

    from context_lifecycle.reconcile.check import run_check

    assert run_check(repo).green
    # CHANGELOG line is scrubbed even if the title had carried a name.
    cl = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert SCRUB_NAME not in cl


def test_changelog_genericizes_scrub_name(monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    vocab = load_scrub_vocabulary(extra_names=[SCRUB_NAME])
    # A title carrying a scrub-target name must be genericized in the public
    # CHANGELOG line (the names belong only on the private archive side).
    from context_lifecycle.reconcile.prune import _changelog_entry
    from context_lifecycle.reconcile.worksheet import ReconcileItem

    item = ReconcileItem(id="x", title=f"shipped {SCRUB_NAME} adapter", status="done", owner="R")
    line = _changelog_entry(item, "2026-06-01", vocab)
    assert SCRUB_NAME not in line
    assert "a private downstream repo" in line


# --- gate: prune refuses when check not green ------------------------------


def test_prune_refuses_when_not_green(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    repo = tmp_path / "R"
    console = repo / ".console"
    console.mkdir(parents=True)
    (console / "reconcile.yaml").write_text(
        "repo: R\nitems:\n  - id: gap\n    status: done\n    doc: []\n",
        encoding="utf-8",
    )
    with pytest.raises(PruneRefused):
        build_plan(repo, private_root=tmp_path / "priv")


def test_changelog_one_line_per_item_not_per_section(tmp_path, monkeypatch):
    """An item whose keywords match several log sections yields ONE CHANGELOG
    line (regression guard for the per-section duplication the OC run hit)."""
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    repo = tmp_path / "MultiMatch"
    console = repo / ".console"
    console.mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "d.md").write_text("d", encoding="utf-8")
    (console / "reconcile.yaml").write_text(
        "repo: MultiMatch\nitems:\n"
        "  - id: coverage-adapter\n"
        "    title: 'Coverage adapter'\n"
        "    status: done\n"
        "    owner: MultiMatch\n"
        "    doc: [docs/d.md]\n",
        encoding="utf-8",
    )
    # Three separate log sections that all match the 'coverage adapter' keywords.
    (console / "log.md").write_text(
        "# Log\n\n"
        "## 2026-05-01 — coverage adapter landed\n\nbody\n\n"
        "## 2026-05-02 — coverage adapter follow-up\n\nbody\n\n"
        "## 2026-05-03 — more coverage adapter polish\n\nbody\n\n",
        encoding="utf-8",
    )
    plan = build_plan(repo, recent_n=0, private_root=tmp_path / "Priv")
    # Multiple sections claimed, but exactly one CHANGELOG line for the item.
    assert len([m for m in plan.moves if m.source == "log.md"]) >= 2
    assert len(plan.changelog_lines) == 1
    assert "coverage-adapter" in plan.changelog_lines[0]


# --- Private repo reconciling itself: retained content + CHANGELOG unscrubbed


def _setup_private_repo(tmp_path) -> Path:
    """A repo whose own name IS the scrub target (a private repo)."""
    repo = tmp_path / SCRUB_NAME
    console = repo / ".console"
    console.mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("design doc", encoding="utf-8")
    (console / "reconcile.yaml").write_text(
        f"repo: {SCRUB_NAME}\n"
        "items:\n"
        "  - id: detectors-trio\n"
        f"    title: 'Detectors trio in {SCRUB_NAME}'\n"
        "    status: done\n"
        f"    owner: {SCRUB_NAME}\n"
        "    doc: [docs/design.md]\n",
        encoding="utf-8",
    )
    log = (
        "# Log\n\n"
        f"## 2026-05-30 — detectors trio shipped\n\nDone inside {SCRUB_NAME}.\n\n"
        f"## 2026-05-29 — recent note\n\n{SCRUB_NAME} keeps its own name.\n\n"
    )
    (console / "log.md").write_text(log, encoding="utf-8")
    (console / "backlog.md").write_text(
        f"# Backlog\n\n## In Progress\n\n- [ ] active in {SCRUB_NAME}\n\n"
        "## Done\n\n- [x] detectors trio\n\n",
        encoding="utf-8",
    )
    return repo


def test_private_repo_prune_keeps_own_name(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    vocab = load_scrub_vocabulary(extra_names=[SCRUB_NAME])
    repo = _setup_private_repo(tmp_path)
    private = tmp_path / "PrivateSide"

    plan = build_plan(repo, vocab=vocab, private_root=private)
    apply_plan(repo, plan, vocab=vocab)

    # Retained .console content keeps the private repo's own name.
    retained = (repo / ".console" / "log.md").read_text(encoding="utf-8")
    retained += (repo / ".console" / "backlog.md").read_text(encoding="utf-8")
    assert SCRUB_NAME in retained
    assert "a private downstream repo" not in retained
    # The CHANGELOG line keeps the name too.
    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert SCRUB_NAME in changelog
    assert "a private downstream repo" not in changelog


def test_public_repo_prune_still_scrubs(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    vocab = load_scrub_vocabulary(extra_names=[SCRUB_NAME])
    repo = _setup_repo(tmp_path, scrub_in_log=True)
    private = tmp_path / "PrivateSide"

    plan = build_plan(repo, vocab=vocab, private_root=private)
    apply_plan(repo, plan, vocab=vocab)

    retained = (repo / ".console" / "log.md").read_text(encoding="utf-8")
    assert SCRUB_NAME not in retained


def test_private_manifest_root_prune_keeps_names(tmp_path, monkeypatch):
    """Pruning the private-manifest repo itself never scrubs retained content."""
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    vocab = load_scrub_vocabulary(extra_names=[SCRUB_NAME])
    repo = tmp_path / "ManifestHost"
    console = repo / ".console"
    console.mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "design.md").write_text("design doc", encoding="utf-8")
    (console / "reconcile.yaml").write_text(
        "repo: ManifestHost\n"
        "items:\n"
        "  - id: registry-renames\n"
        f"    title: 'Registry renames for {SCRUB_NAME}'\n"
        "    status: done\n"
        "    owner: ManifestHost\n"
        "    doc: [docs/design.md]\n",
        encoding="utf-8",
    )
    (console / "log.md").write_text(
        "# Log\n\n"
        f"## 2026-05-30 — registry renames shipped\n\nRenamed {SCRUB_NAME}.\n\n"
        f"## 2026-05-29 — recent note\n\n{SCRUB_NAME} stays named here.\n\n",
        encoding="utf-8",
    )
    (console / "backlog.md").write_text(
        "# Backlog\n\n## In Progress\n\n- [ ] active\n\n## Done\n\n- [x] registry renames\n\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PRIVATE_MANIFEST_DIR", str(repo))

    plan = build_plan(repo, vocab=vocab, private_root=repo)
    apply_plan(repo, plan, vocab=vocab)

    retained = (repo / ".console" / "log.md").read_text(encoding="utf-8")
    retained += (repo / ".console" / "backlog.md").read_text(encoding="utf-8")
    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert SCRUB_NAME in retained
    assert SCRUB_NAME in changelog
    assert "a private downstream repo" not in retained + changelog

