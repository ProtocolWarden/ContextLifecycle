# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the fail-soft worksheet loader (spec §3.1)."""

from __future__ import annotations

from pathlib import Path

from context_lifecycle.reconcile.worksheet import load_worksheet


def _write_worksheet(repo_root: Path, text: str) -> None:
    console = repo_root / ".console"
    console.mkdir(parents=True, exist_ok=True)
    (console / "reconcile.yaml").write_text(text, encoding="utf-8")


def test_missing_worksheet_is_empty_not_error(tmp_path):
    ws = load_worksheet(tmp_path, fallback_repo="MyRepo")
    assert ws.exists is False
    assert ws.items == []
    assert ws.repo == "MyRepo"


def test_loads_valid_items(tmp_path):
    _write_worksheet(
        tmp_path,
        """
schema: 1
repo: MyRepo
items:
  - id: alpha
    title: "Alpha work"
    status: done
    owner: MyRepo
    doc: [docs/a.md]
  - id: beta
    title: "Beta"
    status: incomplete
    owner: OtherRepo
""",
    )
    ws = load_worksheet(tmp_path)
    assert ws.repo == "MyRepo"
    assert [it.id for it in ws.items] == ["alpha", "beta"]
    assert ws.items[0].doc == ("docs/a.md",)
    assert ws.items[1].owner == "OtherRepo"
    assert ws.cross_repo()[0].id == "beta"


def test_malformed_item_skipped_with_warning_never_raises(tmp_path):
    _write_worksheet(
        tmp_path,
        """
repo: MyRepo
items:
  - id: good
    status: done
    doc: [docs/x.md]
  - "not a mapping"
  - id: bad-status
    status: nonsense
  - title: "no id"
    status: done
""",
    )
    ws = load_worksheet(tmp_path)
    assert [it.id for it in ws.items] == ["good"]
    assert len(ws.warnings) == 3


def test_unparseable_yaml_is_fail_soft(tmp_path):
    _write_worksheet(tmp_path, "repo: [unterminated\nitems:\n  - id: x")
    ws = load_worksheet(tmp_path, fallback_repo="MyRepo")
    assert ws.items == []
    assert ws.warnings  # warned, did not raise
    assert ws.repo == "MyRepo"


def test_owner_defaults_to_repo(tmp_path):
    _write_worksheet(
        tmp_path,
        "repo: MyRepo\nitems:\n  - id: x\n    status: done\n    doc: [d.md]\n",
    )
    ws = load_worksheet(tmp_path)
    assert ws.items[0].owner == "MyRepo"
    assert ws.cross_repo() == []
