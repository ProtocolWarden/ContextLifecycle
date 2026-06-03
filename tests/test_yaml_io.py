# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""YAML I/O helper tests (load / load_safe / dump round-trip)."""

from __future__ import annotations

from pathlib import Path

import pytest

from context_lifecycle.io.yaml_io import dump_yaml, load_yaml, load_yaml_safe


def test_dump_then_load_round_trip(tmp_path: Path):
    target = tmp_path / "nested" / "out.yaml"
    data = {"b": 2, "a": 1, "list": [1, 2, 3]}
    dump_yaml(target, data)
    assert target.is_file()
    assert load_yaml(target) == data


def test_load_yaml_raises_on_missing(tmp_path: Path):
    with pytest.raises(OSError):
        load_yaml(tmp_path / "does-not-exist.yaml")


def test_load_yaml_safe_returns_default_on_missing(tmp_path: Path):
    assert load_yaml_safe(tmp_path / "missing.yaml", default={"fallback": True}) == {
        "fallback": True
    }


def test_load_yaml_safe_returns_default_on_parse_error(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: [unterminated\n", encoding="utf-8")
    assert load_yaml_safe(bad, default="sentinel") == "sentinel"


def test_load_yaml_safe_default_is_none(tmp_path: Path):
    assert load_yaml_safe(tmp_path / "missing.yaml") is None
