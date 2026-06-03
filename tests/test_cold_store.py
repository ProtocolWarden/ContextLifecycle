# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the cold-store data layer (.context/.engine/cold.py).

Covers the §2.6 item format parse/validate, the §2.3 one-line surfacing
(glob-matched, tier-filtered, bounded), the empty/malformed-store no-op, and
the never-raises contract (spec §1). Also the build_context integration: cold
lines ride the same router pass as warm docs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ENGINE_DIR = Path(__file__).resolve().parent.parent / "src" / "context_lifecycle" / "context_engine"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _ENGINE_DIR / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# route must be registered as 'cl_route' so cold's `from route import ...`
# shim and route's lazy cold import both resolve under the test harness.
route = _load("cl_route", "route.py")
cold = _load("cold", "cold.py")


# --- fixtures -------------------------------------------------------------

_VALID = """---
topic: projection
paths: ["src/platform_manifest/projection/**"]
created: 2026-06-02
campaign_id: c-2026-06-02-9f3a
consequence:
  acted_on_commit: null
  tests_green: unknown
tier: cold
pinned: false
last_injected: null
---
## Finding
Redaction must run before validation.
## Detail
Long reasoning here.
"""


def _write(dir_: Path, name: str, text: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / name
    p.write_text(text)
    return p


# --- parse_item happy path ------------------------------------------------

def test_parse_item_happy_path(tmp_path):
    p = _write(tmp_path, "projection.md", _VALID)
    item = cold.parse_item(p)
    assert item is not None
    assert item.slug == "projection"
    assert item.topic == "projection"
    assert item.paths == ("src/platform_manifest/projection/**",)
    assert item.tier == "cold"
    assert item.pinned is False
    assert item.campaign_id == "c-2026-06-02-9f3a"
    assert item.acted_on_commit is None
    assert item.last_injected is None
    # finding == first non-empty line of ## Finding (not ## Detail)
    assert item.finding == "Redaction must run before validation."


# --- parse_item degradation (never raises) --------------------------------

@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter at all\n## Finding\nx\n",  # missing frontmatter
        "---\ntopic: x\npaths: [a/**]\n",  # no closing fence
        "---\n: : : not yaml : :\n---\n## Finding\nx\n",  # malformed YAML
        "---\ntopic: x\npaths: notalist\ntier: cold\n---\n## Finding\nx\n",  # paths not list
        "---\ntopic: x\npaths: [a/**]\ntier: warmish\n---\n## Finding\nx\n",  # bad tier
        "---\ntopic: x\npaths: [a/**]\ntier: cold\npinned: maybe\n---\n## Finding\nx\n",  # bad pinned
        "---\ntopic: x\npaths: [a/**]\ntier: cold\n---\n## Detail\nno finding section\n",  # no finding
        "---\ntopic: x\npaths: [a/**]\ntier: cold\n---\n## Finding\n\n",  # empty finding
    ],
)
def test_parse_item_degrades_to_none(tmp_path, text):
    p = _write(tmp_path, "bad.md", text)
    # Must NOT raise; returns None.
    assert cold.parse_item(p) is None


def test_parse_item_unreadable_returns_none(tmp_path):
    # Nonexistent path -> None, no raise.
    assert cold.parse_item(tmp_path / "does-not-exist.md") is None


def test_parse_item_non_utf8_returns_none(tmp_path):
    # A non-UTF-8 / binary knowledge file must degrade to None (the read raises
    # UnicodeDecodeError, a ValueError NOT an OSError) — fail-soft per spec §1.
    p = tmp_path / "binary.md"
    p.write_bytes(b"---\ntopic: \xff\xfe bad bytes\npaths: [a/**]\n---\n")
    assert cold.parse_item(p) is None  # no UnicodeDecodeError escapes


# --- validate_item --------------------------------------------------------

def test_validate_item_clean_for_valid(tmp_path):
    p = _write(tmp_path, "projection.md", _VALID)
    assert cold.validate_item(p) == []
    item = cold.parse_item(p)
    assert cold.validate_item(item) == []


def test_validate_item_reports_specific_problems(tmp_path):
    text = "---\ntopic: ''\npaths: notalist\ntier: bogus\npinned: maybe\n---\n## Detail\nx\n"
    p = _write(tmp_path, "bad.md", text)
    problems = cold.validate_item(p)
    joined = " | ".join(problems)
    assert "topic" in joined
    assert "paths" in joined
    assert "tier" in joined
    assert "pinned" in joined
    assert "Finding" in joined


# --- load_index -----------------------------------------------------------

def test_load_index_drops_malformed_and_readme(tmp_path):
    kn = tmp_path / "knowledge"
    _write(kn, "a.md", _VALID)
    _write(
        kn,
        "b.md",
        _VALID.replace("topic: projection", "topic: other").replace(
            "src/platform_manifest/projection/**", "src/other/**"
        ),
    )
    _write(kn, "broken.md", "no frontmatter\n")
    _write(kn, "README.md", "# index\n")
    items = cold.load_index(kn)
    assert len(items) == 2
    topics = sorted(i.topic for i in items)
    assert topics == ["other", "projection"]


def test_load_index_missing_dir_returns_empty(tmp_path):
    assert cold.load_index(tmp_path / "nope") == []


def test_load_index_one_non_utf8_file_drops_only_itself(tmp_path):
    # A single un-decodable file must NOT blank the whole index: the valid
    # sibling still loads (per-item isolation, spec §2.3 "cold not write-only").
    kn = tmp_path / "knowledge"
    _write(kn, "good.md", _VALID)
    (kn / "binary.md").write_bytes(b"---\ntopic: \xff\xfe\npaths: [a/**]\n---\n")
    items = cold.load_index(kn)
    assert [i.topic for i in items] == ["projection"]


# --- surface_cold ---------------------------------------------------------

def test_surface_cold_matches_glob(tmp_path):
    kn = tmp_path / "knowledge"
    _write(kn, "projection.md", _VALID)
    lines = cold.surface_cold(
        "src/platform_manifest/projection/rules.py", kn, max_items=5
    )
    assert len(lines) == 1
    assert lines[0] == (
        "projection — src/platform_manifest/projection/** — "
        "Redaction must run before validation."
    )


def test_surface_cold_emits_second_matching_glob(tmp_path):
    # An item with multiple paths must name the FIRST glob that matches the
    # target in paths order — here the second entry is the one that hits.
    kn = tmp_path / "knowledge"
    multi = _VALID.replace(
        'paths: ["src/platform_manifest/projection/**"]',
        'paths: ["src/other/**", "src/platform_manifest/projection/**"]',
    )
    _write(kn, "projection.md", multi)
    lines = cold.surface_cold(
        "src/platform_manifest/projection/rules.py", kn, max_items=5
    )
    assert len(lines) == 1
    # The emitted glob is the second path entry (the matching one), with em-dash.
    assert lines[0] == (
        "projection — src/platform_manifest/projection/** — "
        "Redaction must run before validation."
    )


def test_surface_cold_no_cap_when_max_items_zero(tmp_path):
    # max_items <= 0 disables the cap: all matches surface, no truncation note.
    kn = tmp_path / "knowledge"
    for i in range(7):
        _write(kn, f"item{i}.md", _VALID.replace("topic: projection", f"topic: t{i}"))
    lines = cold.surface_cold(
        "src/platform_manifest/projection/rules.py", kn, max_items=0
    )
    assert len(lines) == 7
    assert all("more cold topic" not in line for line in lines)


def test_surface_cold_non_match_is_empty(tmp_path):
    kn = tmp_path / "knowledge"
    _write(kn, "projection.md", _VALID)
    assert cold.surface_cold("src/platform_manifest/topology/x.py", kn, 5) == []


def test_surface_cold_only_cold_tier(tmp_path):
    kn = tmp_path / "knowledge"
    warm = _VALID.replace("tier: cold", "tier: warm")
    _write(kn, "warm.md", warm)
    assert cold.surface_cold("src/platform_manifest/projection/rules.py", kn, 5) == []


def test_surface_cold_cap_with_truncation_note(tmp_path):
    kn = tmp_path / "knowledge"
    for i in range(7):
        item = _VALID.replace("topic: projection", f"topic: t{i}")
        _write(kn, f"item{i}.md", item)
    lines = cold.surface_cold("src/platform_manifest/projection/rules.py", kn, 5)
    assert len(lines) == 6  # 5 + truncation note
    assert "more cold topic" in lines[-1]
    # No silent drop: the note names the count.
    assert "2 more" in lines[-1]


def test_surface_cold_failsoft_missing_dir(tmp_path):
    assert cold.surface_cold("src/a.py", tmp_path / "nope", 5) == []


# --- write_item round-trip ------------------------------------------------

def test_write_item_round_trips(tmp_path):
    kn = tmp_path / "knowledge"
    src = _write(kn, "projection.md", _VALID)
    item = cold.parse_item(src)
    out_dir = tmp_path / "out"
    written = cold.write_item(out_dir, item)
    reparsed = cold.parse_item(written)
    assert reparsed is not None
    assert reparsed.topic == item.topic
    assert reparsed.paths == item.paths
    assert reparsed.tier == item.tier
    assert reparsed.finding == item.finding
    assert cold.validate_item(reparsed) == []


# --- build_context integration --------------------------------------------

def test_build_context_surfaces_cold_item(tmp_path):
    # Hermetic: a cold item whose paths-glob matches the target surfaces one-line.
    _write(
        tmp_path / ".context" / "knowledge",
        "c.md",
        _VALID.replace("src/platform_manifest/projection/**", "src/x/**"),
    )
    block = route.build_context("src/x/thing.py", tmp_path)
    assert "Related cold topics (pull on demand):" in block
    assert "Redaction must run before validation" in block


def test_build_context_cold_only_path(tmp_path):
    # A target with a cold match but NO warm route still returns a non-empty
    # block containing only the cold section (early-return regression guard).
    ctx = tmp_path / ".context"
    ctx.mkdir()
    # No routes.yaml -> warm degrades to nothing.
    kn = ctx / "knowledge"
    _write(kn, "a.md", _VALID.replace("src/platform_manifest/projection/**", "src/a/**"))
    block = route.build_context("src/a/thing.py", tmp_path)
    assert block != ""
    assert "Related cold topics (pull on demand):" in block
    assert "Relevant conventions for" not in block  # no warm header


def test_build_context_warm_and_cold_together(tmp_path):
    # Hermetic: a target with BOTH a warm route+leaf doc and a matching cold item
    # — warm header first, then the cold section.
    ctx = tmp_path / ".context"
    _write(
        ctx,
        "routes.yaml",
        'engine_compat: ">=0.2 <0.3"\n'
        "budget:\n  max_docs_per_edit: 3\n"
        'routes:\n  - match: "src/x/**"\n'
        '    inject: ["docs/inject/d.md"]\n    priority: 10\n',
    )
    _write(
        tmp_path / "docs" / "inject",
        "d.md",
        "## Inject\n- Always validate first.\n\n## Reference\nmore\n",
    )
    _write(
        ctx / "knowledge",
        "c.md",
        _VALID.replace("src/platform_manifest/projection/**", "src/x/**"),
    )
    block = route.build_context("src/x/thing.py", tmp_path)
    assert "Relevant conventions for" in block
    warm_idx = block.index("Relevant conventions for")
    cold_idx = block.index("Related cold topics (pull on demand):")
    assert warm_idx < cold_idx


def test_load_cold_cap_reads_config_else_defaults(tmp_path):
    # Default when no routes.yaml / no key; reads the budget key when present;
    # fail-soft to default on a malformed value (mirrors max_docs_per_edit).
    ctx = tmp_path / ".context"
    ctx.mkdir()
    assert route.load_cold_cap(ctx) == route.COLD_SURFACE_CAP  # no routes.yaml
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\nbudget:\n  max_cold_surface_per_edit: 2\n"
    )
    assert route.load_cold_cap(ctx) == 2
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\nbudget:\n  max_cold_surface_per_edit: nope\n"
    )
    assert route.load_cold_cap(ctx) == route.COLD_SURFACE_CAP  # malformed -> default


def test_build_context_honours_configured_cold_cap(tmp_path):
    # End-to-end: the configured cap truncates the cold surface in build_context.
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\nbudget:\n  max_cold_surface_per_edit: 1\n"
    )
    kn = ctx / "knowledge"
    for i in range(3):
        _write(
            kn,
            f"item{i}.md",
            _VALID.replace("topic: projection", f"topic: t{i}").replace(
                "src/platform_manifest/projection/**", "src/a/**"
            ),
        )
    block = route.build_context("src/a/thing.py", tmp_path)
    assert "more cold topic" in block  # capped at 1 -> truncation note present
    assert "2 more" in block


def test_build_context_never_raises_on_cold_failure(monkeypatch, tmp_path):
    # If cold surfacing blows up, build_context still returns the warm block
    # (degrade) and never raises (spec §1).
    def _boom(*a, **k):
        raise RuntimeError("cold exploded")

    monkeypatch.setattr(cold, "surface_cold", _boom)
    ctx = tmp_path / ".context"
    _write(
        ctx,
        "routes.yaml",
        'engine_compat: ">=0.2 <0.3"\n'
        'routes:\n  - match: "src/loader.py"\n'
        '    inject: ["docs/inject/loader.md"]\n    priority: 10\n',
    )
    _write(tmp_path / "docs" / "inject", "loader.md", "## Inject\n- Fail-closed.\n")
    block = route.build_context("src/loader.py", tmp_path)
    # Warm content still present; cold blew up but was swallowed; no exception.
    assert "loader.md" in block
