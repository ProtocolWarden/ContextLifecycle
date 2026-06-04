# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the single-source scrub vocabulary (spec §1 / AC1).

Tests use synthetic forbidden names (never the real scrub targets) so this
tracked test file stays boundary-clean. The detection logic is identical.
"""

from __future__ import annotations

import json

from context_lifecycle.reconcile.scrub import load_scrub_vocabulary

# Synthetic stand-ins for the real (artifact-sourced) scrub targets.
LONG_NAME = "SecretRepo"
SHORT_NAME = "SR"


def _write_artifact(tmp_path, names):
    art = tmp_path / "boundary.json"
    art.write_text(json.dumps({"forbidden_names": names}), encoding="utf-8")
    return art


def test_vocabulary_sourced_from_artifact(tmp_path, monkeypatch):
    art = _write_artifact(tmp_path, [LONG_NAME, SHORT_NAME])
    monkeypatch.setenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", str(art))
    vocab = load_scrub_vocabulary()
    assert set(vocab.names) == {LONG_NAME, SHORT_NAME}
    assert vocab.provenance == str(art)


def test_long_name_matches_substring_case_insensitive(tmp_path, monkeypatch):
    art = _write_artifact(tmp_path, [LONG_NAME])
    monkeypatch.setenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", str(art))
    vocab = load_scrub_vocabulary()
    assert vocab.matches(f"owned by {LONG_NAME.lower()} team") == [LONG_NAME]
    assert vocab.matches("nothing here") == []


def test_short_name_word_boundary_excludes_detector_ids(tmp_path, monkeypatch):
    art = _write_artifact(tmp_path, [SHORT_NAME])
    monkeypatch.setenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", str(art))
    vocab = load_scrub_vocabulary()
    # Bare token matches; embedded in an id like "SR2" does NOT.
    assert vocab.matches(f"the {SHORT_NAME} repo") == [SHORT_NAME]
    assert vocab.matches(f"{SHORT_NAME}2 detector") == []


def test_changing_one_config_changes_detection_everywhere(tmp_path, monkeypatch):
    # AC1: detection follows the single config source.
    art = _write_artifact(tmp_path, [LONG_NAME])
    monkeypatch.setenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", str(art))
    assert load_scrub_vocabulary().matches(LONG_NAME) == [LONG_NAME]
    # Rewrite the same artifact dropping the name -> detection stops.
    art.write_text(json.dumps({"forbidden_names": []}), encoding="utf-8")
    assert load_scrub_vocabulary().matches(LONG_NAME) == []


def test_no_source_yields_empty_vocab(tmp_path, monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    # Also disable the RepoGraph-registry fallback so "no source" is genuinely
    # no source (otherwise a machine with a registered private manifest resolves
    # a real artifact). Patch the function _artifact_path imports lazily.
    monkeypatch.setattr(
        "context_lifecycle.reconcile.privacy._discover_via_repograph",
        lambda: None,
    )
    vocab = load_scrub_vocabulary()
    assert vocab.is_empty()


def test_extra_names_injection(monkeypatch):
    monkeypatch.delenv("REPOGRAPH_BOUNDARY_ARTIFACT_FILE", raising=False)
    monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
    vocab = load_scrub_vocabulary(extra_names=[LONG_NAME])
    assert vocab.matches(LONG_NAME) == [LONG_NAME]
