# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for context_lifecycle.ledger.capture.

Candidate format, dedup (a re-firing signal does not duplicate), header
bootstrap, no-dedup against a promoted entry, and LedgerUnavailable fail-soft.
"""

from __future__ import annotations

import importlib

from context_lifecycle.ledger.capture import (
    LEDGER_SUBPATH,
    LedgerUnavailable,
    capture,
    ledger_path,
)


def test_capture_writes_candidate(tmp_path):
    path, written = capture(
        "pr-closed-by-human", "RepoGraph#42: x", date="2026-06-16", private_root=tmp_path
    )
    assert written is True
    assert path == tmp_path / LEDGER_SUBPATH
    text = path.read_text(encoding="utf-8")
    assert "## Entries" in text  # header bootstrapped
    assert (
        "- [ ] CANDIDATE 2026-06-16 pr-closed-by-human — RepoGraph#42: x — judgment: ___"
        in text
    )


def test_capture_dedups_same_signal_context(tmp_path):
    capture("pr-closed-by-human", "RepoGraph#42: x", date="2026-06-16", private_root=tmp_path)
    path, written = capture(
        "pr-closed-by-human", "RepoGraph#42: x", date="2026-06-17", private_root=tmp_path
    )
    assert written is False  # different date, same signal+context → deduped
    assert path.read_text(encoding="utf-8").count("RepoGraph#42: x") == 1


def test_capture_distinct_context_not_deduped(tmp_path):
    capture("pr-closed-by-human", "RepoGraph#42: x", private_root=tmp_path)
    _, written = capture("pr-closed-by-human", "RepoGraph#43: y", private_root=tmp_path)
    assert written is True


def test_capture_does_not_dedup_against_promoted(tmp_path):
    # A promoted (non-candidate) entry mentioning the same context must NOT
    # suppress a fresh candidate — promotion removed the CANDIDATE marker.
    path = tmp_path / LEDGER_SUBPATH
    path.parent.mkdir(parents=True)
    path.write_text(
        "## Entries\n- **2026-06-01** — RepoGraph#42: x → **judgment:** check owns-edge\n",
        encoding="utf-8",
    )
    _, written = capture("pr-closed-by-human", "RepoGraph#42: x", private_root=tmp_path)
    assert written is True


def test_capture_empty_signal_rejected(tmp_path):
    try:
        capture("   ", "ctx", private_root=tmp_path)
    except ValueError:
        return
    raise AssertionError("empty signal should raise ValueError")


def test_ledger_path_unavailable_without_private_root(monkeypatch):
    capture_module = importlib.import_module("context_lifecycle.ledger.capture")
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    monkeypatch.setattr(capture_module, "resolve_private_root", _raise_unavailable)
    try:
        ledger_path()
    except LedgerUnavailable:
        return
    raise AssertionError("expected LedgerUnavailable")


def _raise_unavailable():
    from context_lifecycle.reconcile.privacy import PrivateArchiveUnavailable

    raise PrivateArchiveUnavailable("no private-manifest root found")
