# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for ``cl context init`` scaffolding (context_engine.scaffold)."""

from __future__ import annotations

from context_lifecycle.context_engine import (
    ENGINE_FILES,
    engine_source_files,
)
from context_lifecycle.context_engine.scaffold import InitReport, init_context


def test_init_returns_init_report(tmp_path):
    report = init_context(tmp_path)
    assert isinstance(report, InitReport)


def test_engine_source_files_lists_existing_modules():
    files = engine_source_files()
    names = {p.name for p in files}
    assert names == set(ENGINE_FILES)
    assert all(p.is_file() for p in files)


def test_init_fresh_creates_engine_and_state(tmp_path):
    report = init_context(tmp_path)
    # all engine files created
    for name in ENGINE_FILES:
        assert (tmp_path / ".context" / ".engine" / name).is_file()
        assert f".context/.engine/{name}" in report.created
    # state surfaces created
    assert (tmp_path / ".context" / "routes.yaml").is_file()
    assert (tmp_path / "docs" / "inject" / "README.md").is_file()
    assert (tmp_path / ".context" / "knowledge" / "README.md").is_file()
    assert report.skipped == []


def test_init_is_idempotent_refresh_engine_keep_state(tmp_path):
    init_context(tmp_path)
    # user edits their routes table; init must NOT clobber it
    routes = tmp_path / ".context" / "routes.yaml"
    routes.write_text("engine_compat: \">=0.2 <0.3\"\nroutes:\n  - match: \"x/**\"\n")
    report = init_context(tmp_path)
    # engine refreshed (not created), state kept (not overwritten)
    assert len(report.refreshed) == len(ENGINE_FILES)
    assert report.created == []
    assert ".context/routes.yaml" in report.skipped
    assert "x/**" in routes.read_text()  # preserved


def test_scaffolded_routes_stamps_engine_compat(tmp_path):
    init_context(tmp_path)
    assert "engine_compat:" in (tmp_path / ".context" / "routes.yaml").read_text()


def test_scaffolded_engine_byte_matches_package_source(tmp_path):
    init_context(tmp_path)
    from context_lifecycle.context_engine import ENGINE_DIR

    for name in ENGINE_FILES:
        scaffolded = (tmp_path / ".context" / ".engine" / name).read_bytes()
        source = (ENGINE_DIR / name).read_bytes()
        assert scaffolded == source, name
