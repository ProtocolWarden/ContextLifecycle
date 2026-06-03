# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Cold->warm materialization (PROTOTYPE) — spec §2.2 distill / §3 warm format.

Kept separate from consolidate.py so the "what a promoted cold item becomes"
transform is independently testable. Two PURE planners and the apply-only
mutators:

  (a) leaf_doc_for(item) -> (Path, bullet): the target ``docs/inject/<topic>.md``
      and the ``## Inject`` bullet derived from the cold item's Finding. The cold
      Detail stays in cold as the ``## Reference`` pointer — never duplicated
      into warm.
  (b) route_entry_for(item) -> list[dict]: a routes.yaml entry per item path
      glob ``{match, inject: [<leaf doc>], priority}``.
  (c) materialize(...) (apply-only) writes/extends the leaf doc (APPENDING to an
      existing ## Inject section, never clobbering), adds the route idempotently
      (preserving engine_compat/budget), and rewrites the cold item with tier
      flipped cold->warm and last_injected stamped, so cold.surface_cold (which
      filters tier=='cold') no longer double-surfaces it.

Pure stdlib + PyYAML; all YAML round-trips go through yaml.safe_load/safe_dump.
Mutators never raise to the caller (spec §1).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import yaml

INJECT_HEADING = "## Inject"
DEFAULT_PRIORITY = 50
ROUTES_FILE = "routes.yaml"


def _load_route():
    try:  # pragma: no cover - import shim
        import route as _route  # noqa: PLC0415

        return _route
    except ImportError:  # pragma: no cover
        p = Path(__file__).resolve().parent / "route.py"
        spec = importlib.util.spec_from_file_location("cl_route", p)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("cl_route", mod)
        spec.loader.exec_module(mod)
        return mod


def _load_cold():
    try:  # pragma: no cover - import shim
        import cold as _cold  # noqa: PLC0415

        return _cold
    except ImportError:  # pragma: no cover
        p = Path(__file__).resolve().parent / "cold.py"
        spec = importlib.util.spec_from_file_location("cl_cold", p)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("cl_cold", mod)
        spec.loader.exec_module(mod)
        return mod


def leaf_doc_rel(item) -> str:
    """The repo-relative leaf-doc path for a cold item: docs/inject/<topic>.md.

    Uses the item ``topic`` (already a routing key) when present, else the slug.
    """
    name = (item.topic or item.slug).strip().replace("/", "-")
    return f"docs/inject/{name}.md"


def inject_bullet(item) -> str:
    """The single ## Inject bullet derived from the cold Finding (one line)."""
    return f"- {item.finding}"


def leaf_doc_for(item, root: Path) -> tuple[Path, str]:
    """PURE: compute (absolute leaf-doc path, ## Inject bullet). No writes."""
    return root / leaf_doc_rel(item), inject_bullet(item)


def route_entry_for(item) -> list[dict]:
    """PURE: compute routes.yaml entries (one per item path glob). No writes."""
    leaf = leaf_doc_rel(item)
    return [
        {"match": glob, "inject": [leaf], "priority": DEFAULT_PRIORITY}
        for glob in item.paths
    ]


def _write_leaf_doc(leaf_path: Path, bullet: str) -> None:
    """Write/extend the leaf doc, APPENDING the bullet to ## Inject if present.

    Preserves any existing ## Inject bullets and any ## Reference section by
    re-reading the current ## Inject body via route.extract_inject. Idempotent:
    a bullet already present is not duplicated.
    """
    route = _load_route()
    leaf_path.parent.mkdir(parents=True, exist_ok=True)

    if leaf_path.exists():
        existing = route.extract_inject(leaf_path)
        if existing == route.MISSING_DOC:
            existing = ""
        bullets = [
            ln for ln in existing.splitlines() if ln.strip()
        ]
        if bullet.strip() not in {b.strip() for b in bullets}:
            bullets.append(bullet)
        # Rebuild: replace the whole file with a clean ## Inject section,
        # preserving everything below the first non-Inject heading (## Reference).
        text = leaf_path.read_text()
        tail = _section_tail(text)
        new = INJECT_HEADING + "\n" + "\n".join(bullets) + "\n"
        if tail:
            new += "\n" + tail
        leaf_path.write_text(new)
    else:
        leaf_path.write_text(f"{INJECT_HEADING}\n{bullet}\n")


def _section_tail(text: str) -> str:
    """Return everything from the first non-## Inject heading onward (verbatim).

    Lets _write_leaf_doc preserve a ## Reference section while it rebuilds the
    ## Inject section.
    """
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            if stripped == INJECT_HEADING:
                capturing = False
                continue
            capturing = True
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _add_route(context_dir: Path, entries: list[dict]) -> None:
    """Add route entries to routes.yaml idempotently, preserving other keys.

    An entry is skipped when an equivalent (match, inject-contains-leaf) pair is
    already present. engine_compat/budget and existing routes are preserved.
    """
    routes_path = context_dir / ROUTES_FILE
    try:
        data = yaml.safe_load(routes_path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    routes = data.get("routes")
    if not isinstance(routes, list):
        routes = []

    def _present(match: str, leaf: str) -> bool:
        for r in routes:
            if not isinstance(r, dict):
                continue
            if r.get("match") == match and leaf in (r.get("inject") or []):
                return True
        return False

    for entry in entries:
        match = entry["match"]
        leaf = entry["inject"][0]
        if not _present(match, leaf):
            routes.append(entry)

    data["routes"] = routes
    routes_path.parent.mkdir(parents=True, exist_ok=True)
    routes_path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))


def materialize(item, root: Path, run_date: str) -> None:
    """APPLY-ONLY: write the leaf doc + route, flip the cold item cold->warm.

    Per-step isolation, never raises (spec §1). After this the fact is warm
    (tracked for §2.4 decay via last_injected) and cold.surface_cold no longer
    surfaces it (tier filter).
    """
    try:
        cold = _load_cold()
        leaf_path, bullet = leaf_doc_for(item, root)
        _write_leaf_doc(leaf_path, bullet)
        _add_route(root / ".context", route_entry_for(item))

        promoted = replace(item, tier="warm", last_injected=run_date)
        cold.write_item(root / ".context" / "knowledge", promoted)
    except Exception:
        return
