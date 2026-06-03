# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the context-injection router prototype (.context/.engine/route.py).

Pure routing logic: glob matching, all-matches selection, budget + priority,
engine_compat degradation, and ## Inject extraction. No hook wiring is tested
(the engine ships dark; wiring is a later, gated phase).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ENGINE = Path(__file__).resolve().parent.parent / "src" / "context_lifecycle" / "context_engine" / "route.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("cl_route", _ENGINE)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec so the frozen dataclass can resolve its module's
    # namespace (dataclasses introspects sys.modules[cls.__module__]).
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


route = _load_module()


# --- glob matching -------------------------------------------------------

@pytest.mark.parametrize(
    "glob,target,expected",
    [
        ("src/platform_manifest/loader.py", "src/platform_manifest/loader.py", True),
        ("src/platform_manifest/loader.py", "src/platform_manifest/cli.py", False),
        ("src/platform_manifest/projection/**", "src/platform_manifest/projection/rules.py", True),
        ("src/platform_manifest/projection/**", "src/platform_manifest/projection/a/b.py", True),
        ("src/platform_manifest/projection/**", "src/platform_manifest/projection", True),
        ("src/platform_manifest/projection/**", "src/platform_manifest/topology/x.py", False),
        ("src/platform_manifest/schemas/*.json", "src/platform_manifest/schemas/x.json", True),
        ("src/platform_manifest/schemas/*.json", "src/platform_manifest/schemas/sub/x.json", False),
        ("scripts/provision*.sh", "scripts/provision.sh", True),
        ("scripts/provision*.sh", "scripts/provision-machine.sh", True),
        ("scripts/provision*.sh", "scripts/clone-repos.sh", False),
        # interior `/**/` keeps the path structure — must NOT collapse to `ab`
        ("a/**/b", "ab", False),
        ("a/**/b", "a/b", True),
        ("a/**/b", "a/x/b", True),
        ("a/**/b", "a/x/y/b", True),
    ],
)
def test_glob_to_regex(glob, target, expected):
    assert bool(route._glob_to_regex(glob).match(target)) is expected


# --- version range -------------------------------------------------------

def test_version_in_range():
    assert route._version_in_range((0, 2), ">=0.2 <0.3") is True
    assert route._version_in_range((0, 3), ">=0.2 <0.3") is False
    assert route._version_in_range((0, 1), ">=0.2 <0.3") is False
    assert route._version_in_range((0, 2), "") is False          # missing → degrade
    assert route._version_in_range((0, 2), "garbage") is False    # unparseable → degrade


# --- all-matches selection, priority, budget -----------------------------

def _routes():
    return [
        route.Route(match="src/a/**", inject=("docs/a.md", "docs/shared.md"), priority=20),
        route.Route(match="src/a/x.py", inject=("docs/x.md",), priority=10),
        route.Route(match="src/b/**", inject=("docs/b.md",), priority=5),
    ]

def test_all_matches_fires_every_rule_and_dedupes():
    # src/a/x.py matches both the glob rule and the exact rule.
    docs = route.select_docs("src/a/x.py", _routes(), max_docs=0)
    assert docs == ["docs/x.md", "docs/a.md", "docs/shared.md"]  # priority 10 then 20, deduped

def test_no_match_returns_empty():
    assert route.select_docs("src/c/y.py", _routes(), max_docs=0) == []

def test_budget_caps_by_priority():
    docs = route.select_docs("src/a/x.py", _routes(), max_docs=2)
    assert docs == ["docs/x.md", "docs/a.md"]  # lowest-priority-number wins, capped at 2

def test_leading_dot_slash_normalised():
    docs = route.select_docs("./src/b/z.py", _routes(), max_docs=0)
    assert docs == ["docs/b.md"]


def test_dotfile_target_not_mangled():
    # `.lstrip("./")` would strip the leading dot and break dotfile routes;
    # only a literal "./" prefix should be removed.
    routes = [route.Route(match=".github/workflows/ci.yml", inject=("docs/ci.md",), priority=10)]
    assert route.select_docs(".github/workflows/ci.yml", routes, max_docs=0) == ["docs/ci.md"]
    # a real dot-prefixed path passed through select still matches its glob
    assert route.select_docs("./.github/workflows/ci.yml", routes, max_docs=0) == ["docs/ci.md"]


def test_negative_budget_is_uncapped():
    # A negative budget is a plausible typo; it must NOT be used as a slice
    # index (which would silently drop docs from the end). Treat as uncapped.
    full = route.select_docs("src/a/x.py", _routes(), max_docs=0)
    assert route.select_docs("src/a/x.py", _routes(), max_docs=-1) == full
    assert route.select_docs("src/a/x.py", _routes(), max_docs=-2) == full


def test_shared_doc_ranked_by_min_priority():
    # A doc injected by a broad high-number route AND a specific low-number
    # route must be ranked by the lower number and survive a tight budget.
    routes = [
        route.Route(match="src/**", inject=("shared",), priority=90),
        route.Route(match="src/x.py", inject=("filler",), priority=50),
        route.Route(match="src/x.py", inject=("shared", "crit"), priority=1),
    ]
    kept, dropped = route.select_docs_split("src/x.py", routes, max_docs=2)
    assert kept == ["shared", "crit"]   # both priority-1, ranked above filler
    assert dropped == ["filler"]


def test_budget_drop_is_reported(tmp_path):
    # Spec §3: no silent truncation — over-budget docs are named.
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.md").write_text(f"## Inject\nbody {name}\n")
    routes = [
        route.Route(match="x", inject=(f"{tmp_path.name}/a.md",), priority=1),
        route.Route(match="x", inject=(f"{tmp_path.name}/b.md", f"{tmp_path.name}/c.md"), priority=2),
    ]
    kept, dropped = route.select_docs_split("x", routes, max_docs=1)
    assert len(kept) == 1
    assert len(dropped) == 2


# --- ## Inject extraction ------------------------------------------------

def test_extract_inject_only(tmp_path):
    doc = tmp_path / "leaf.md"
    doc.write_text(
        "<!-- c -->\n"
        "## Inject\n- keep this\n- and this\n\n"
        "## Reference\n- DROP this reference line\n"
    )
    body = route.extract_inject(doc)
    assert "keep this" in body
    assert "and this" in body
    assert "DROP this" not in body

def test_extract_inject_missing_section(tmp_path):
    doc = tmp_path / "leaf.md"
    doc.write_text("## Reference\nonly reference here\n")
    assert route.extract_inject(doc) == ""


def test_extract_inject_missing_file_is_sentinel(tmp_path):
    # A nonexistent file is a route config error, distinct from an empty
    # section ('').
    assert route.extract_inject(tmp_path / "nope.md") == route.MISSING_DOC


# --- load_routes fail-closed contract ------------------------------------

def test_load_routes_non_int_budget_degrades(tmp_path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\n"
        "budget:\n  max_docs_per_edit: foo\n"
        "routes:\n  - match: src/a.py\n    inject: [docs/a.md]\n"
    )
    # Must not raise; malformed budget degrades to uncapped (0).
    routes, max_docs, compatible = route.load_routes(ctx)
    assert compatible is True
    assert max_docs == 0
    assert len(routes) == 1


def test_load_routes_non_int_priority_degrades(tmp_path):
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\n"
        "routes:\n  - match: src/a.py\n    inject: [docs/a.md]\n    priority: high\n"
    )
    # Must not raise; malformed priority defaults to 100.
    routes, _max_docs, compatible = route.load_routes(ctx)
    assert compatible is True
    assert routes[0].priority == 100


# --- end-to-end (hermetic; consumer-repo routing is tested in the consumer) ---
# Note: tests asserting a SPECIFIC consumer's routes.yaml / leaf-doc content live
# in that consumer repo (e.g. PlatformManifest), not here — the engine package
# ships no routing table of its own.

def test_build_context_real_routes_matches_loader(tmp_path):
    # Hermetic: a route + leaf doc; ## Inject injected, ## Reference excluded.
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "routes.yaml").write_text(
        'engine_compat: ">=0.2 <0.3"\n'
        'routes:\n  - match: "src/loader.py"\n'
        '    inject: ["docs/inject/loader.md"]\n    priority: 10\n'
    )
    doc = tmp_path / "docs" / "inject"
    doc.mkdir(parents=True)
    (doc / "loader.md").write_text(
        "## Inject\n- Fail-closed on visibility.\n\n## Reference\ndefault_config_path()\n"
    )
    block = route.build_context("src/loader.py", tmp_path)
    assert "loader.md" in block
    assert "Fail-closed on visibility" in block
    assert "default_config_path()" not in block  # ## Reference excluded

def test_build_context_unknown_path_is_empty(tmp_path):
    assert route.build_context("README.md", tmp_path) == ""


def test_build_context_missing_doc_distinct_from_empty(tmp_path, monkeypatch):
    # A route pointing at a nonexistent doc produces a distinct 'not found'
    # diagnostic, not the 'no ## Inject section' wording (which means empty).
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (tmp_path / "good.md").write_text("## Inject\nreal body\n")
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\n"
        "routes:\n"
        "  - match: src/a.py\n    inject: [good.md, MISSING.md]\n"
    )
    block = route.build_context("src/a.py", tmp_path)
    assert "real body" in block
    assert "not found (broken route): MISSING.md" in block
    assert "no ## Inject section" not in block


def test_build_context_all_missing_reports_not_no_match(tmp_path):
    # A fully broken route set must be distinguishable from no-match ('').
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\n"
        "routes:\n  - match: src/a.py\n    inject: [MISSING.md]\n"
    )
    block = route.build_context("src/a.py", tmp_path)
    assert "not found (broken route): MISSING.md" in block


def test_build_context_negative_budget_still_injects(tmp_path):
    # End-to-end: a negative budget must not silently turn injection off.
    ctx = tmp_path / ".context"
    ctx.mkdir()
    (tmp_path / "good.md").write_text("## Inject\nreal body\n")
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\n"
        "budget:\n  max_docs_per_edit: -1\n"
        "routes:\n  - match: src/a.py\n    inject: [good.md]\n"
    )
    block = route.build_context("src/a.py", tmp_path)
    assert "real body" in block


def test_build_context_budget_drop_named(tmp_path):
    # End-to-end: over-budget docs are named (spec §3 no silent truncation).
    ctx = tmp_path / ".context"
    ctx.mkdir()
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.md").write_text(f"## Inject\nbody {name}\n")
    (ctx / "routes.yaml").write_text(
        "engine_compat: '>=0.2 <0.3'\n"
        "budget:\n  max_docs_per_edit: 1\n"
        "routes:\n  - match: src/a.py\n    inject: [a.md, b.md, c.md]\n"
    )
    block = route.build_context("src/a.py", tmp_path)
    assert "body a" in block
    assert "body b" not in block
    assert "dropped 2 over-budget doc(s): b.md, c.md" in block


# Note: the leaf-doc anti-staleness guard (importing the consumer's src symbols
# named in its leaf docs) is consumer-specific and lives in the consumer repo
# (e.g. PlatformManifest), not in the engine package's tests.
