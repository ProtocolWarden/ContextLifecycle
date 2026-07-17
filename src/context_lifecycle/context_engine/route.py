# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Context-injection router (PROTOTYPE).

Given a target file path, decide which warm leaf docs to inject, honouring:
  - all-matches routing (every matching rule fires, not first-match-wins)
  - an injection budget (max_docs_per_edit) with priority ordering
  - engine/routes version compatibility (engine_compat), degrading to NO
    injection on mismatch rather than misparsing

This is the PROTOTYPE home (`.context/.engine/`). Per the spec
(docs/architecture/context-injection-spec.md §1) the production engine is
authored in the ContextLifecycle repo and provisioned in; this local copy
exists to validate the design and ship dark behind `injection.enabled`.

Pure stdlib + PyYAML (already a project dependency). Never raises to its caller
in __main__: any failure prints nothing and exits 0, so it can never block a
tool call when wired into a hook.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import yaml

# Bump when the routes.yaml schema changes. routes.yaml carries an
# `engine_compat:` range; if this version is outside it, we degrade to no
# injection (§1 of the spec).
SCHEMA_VERSION = (0, 2)

ROUTES_FILE = "routes.yaml"
INJECT_HEADING = "## Inject"

# Separate, smaller cap for cold one-line surfacing (spec §9 "cold-surfacing
# noise"): protects the high-attention position the warm budget already guards.
# Conservative default; overridable via routes.yaml `budget.max_cold_surface_per_edit`
# so all breadth tuning lives in one config surface alongside max_docs_per_edit.
COLD_SURFACE_CAP = 5

# One-line citation protocol note appended to the cold block (D3 attribution
# scheme A): the instruction travels WITH the injected data, so every consumer
# learns the ``Context-Used:`` trailer protocol without per-consumer prompt
# changes. Must NOT start with ``[`` (so `_cold_slug_from_line` returns None
# for it) and is appended only to the rendered block — never to `cold_lines` —
# so the `cold_surfaced` count and `cold_slugs` telemetry are unaffected.
COLD_CITATION_NOTE = (
    "(cite: if you act on a [slug] item above, add the git trailer "
    '"Context-Used: <slug>" to that commit)'
)


@dataclass(frozen=True)
class Route:
    match: str
    inject: tuple[str, ...]
    priority: int  # lower = higher precedence when the budget truncates


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    """Translate a path glob to a regex.

    Supports `**` (any number of path segments, including none) and `*` (any
    run of characters within a single segment). Anchored at both ends.
    """
    out: list[str] = ["^"]
    i = 0
    n = len(glob)
    while i < n:
        # `/**` — a subtree wildcard.
        if glob[i] == "/" and glob[i + 1 : i + 3] == "**":
            # Trailing `/**` is an OPTIONAL subtree: matches the dir itself AND
            # anything under it. So `a/**` matches `a`, `a/b`, `a/b/c`.
            # An interior `/**/` (e.g. `a/**/b`) must keep the path structure
            # around it: it matches zero or more intervening segments but still
            # requires the separators, so `a/**/b` matches `a/b`, `a/x/b` but
            # NOT `ab`.
            if i + 3 < n and glob[i + 3] == "/":
                out.append("/(.*/)?")
                i += 4
                continue
            out.append("(/.*)?")
            i += 3
            if i < n and glob[i] == "/":
                i += 1
            continue
        if glob[i] == "*":
            if i + 1 < n and glob[i + 1] == "*":
                # bare `**` (not preceded by `/`) — match across segments.
                out.append(".*")
                i += 2
                if i < n and glob[i] == "/":
                    i += 1
                continue
            out.append("[^/]*")
            i += 1
            continue
        out.append(re.escape(glob[i]))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def _version_in_range(version: tuple[int, int], spec: str) -> bool:
    """Check a `>=A.B <C.D` range string against a (major, minor) version.

    Conservative: any unparseable spec returns False (degrade to no injection).
    """
    if not spec:
        return False
    ok = True
    for clause in spec.split():
        m = re.match(r"^(>=|<=|<|>|==)?(\d+)\.(\d+)$", clause.strip())
        if not m:
            return False
        op = m.group(1) or "=="
        bound = (int(m.group(2)), int(m.group(3)))
        if op == ">=":
            ok = ok and version >= bound
        elif op == "<=":
            ok = ok and version <= bound
        elif op == ">":
            ok = ok and version > bound
        elif op == "<":
            ok = ok and version < bound
        else:
            ok = ok and version == bound
    return ok


def load_routes(context_dir: Path) -> tuple[list[Route], int, bool]:
    """Load `<context_dir>/routes.yaml`.

    Returns (routes, max_docs_per_edit, compatible). On any problem (missing
    file, parse error, version mismatch) returns ([], 0, False) so callers
    degrade to no injection.
    """
    routes_path = context_dir / ROUTES_FILE
    try:
        data = yaml.safe_load(routes_path.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return [], 0, False

    if not _version_in_range(SCHEMA_VERSION, str(data.get("engine_compat", ""))):
        return [], 0, False

    budget = data.get("budget", {}) or {}
    try:
        max_docs = int(budget.get("max_docs_per_edit", 0) or 0)
    except (TypeError, ValueError):
        # Malformed budget (e.g. a YAML typo `max_docs_per_edit: foo`) →
        # uncapped rather than raising out of a fail-closed loader.
        max_docs = 0

    routes: list[Route] = []
    for entry in data.get("routes", []) or []:
        match = entry.get("match")
        inject = entry.get("inject") or []
        if not match or not inject:
            continue
        try:
            priority = int(entry.get("priority", 100))
        except (TypeError, ValueError):
            # Malformed priority (e.g. `priority: high`) → default precedence
            # rather than raising out of a fail-closed loader.
            priority = 100
        routes.append(
            Route(
                match=match,
                inject=tuple(inject),
                priority=priority,
            )
        )
    return routes, max_docs, True


def select_docs(target: str, routes: list[Route], max_docs: int) -> list[str]:
    """All-matches selection: every matching rule contributes its docs.

    Convenience wrapper around :func:`select_docs_split` that returns only the
    kept docs (the ones that win the budget). See that function for the dedupe
    and ordering semantics.
    """
    kept, _dropped = select_docs_split(target, routes, max_docs)
    return kept


def select_docs_split(
    target: str, routes: list[Route], max_docs: int
) -> tuple[list[str], list[str]]:
    """All-matches selection, returning (kept, budget_dropped).

    Every matching rule contributes its docs. A doc injected by several routes
    is deduped and ranked by the MINIMUM priority across all matching routes
    (lower number = higher precedence), so a specific high-precedence route
    wins over a broad low-precedence one that merely mentions the doc first.
    Order by ascending priority, then cap at max_docs (non-positive = uncapped;
    a negative value is NOT used as a slice index). `budget_dropped` lists the
    docs cut by the cap, in priority order, so the caller can report them
    (spec §3: no silent truncation).
    """
    # Strip only a leading literal "./" prefix — NOT a character set, which
    # would mangle dot-prefixed paths like ".github/...".
    if target.startswith("./"):
        target = target[2:]
    best: dict[str, int] = {}
    order: list[str] = []
    for route in routes:
        if not _glob_to_regex(route.match).match(target):
            continue
        for doc in route.inject:
            if doc not in best:
                best[doc] = route.priority
                order.append(doc)
            elif route.priority < best[doc]:
                best[doc] = route.priority
    # Stable sort by priority, preserving first-sight order within a tie.
    order.sort(key=lambda doc: best[doc])
    if max_docs > 0 and len(order) > max_docs:
        return order[:max_docs], order[max_docs:]
    return order, []


# Sentinel distinguishing "file does not exist / unreadable" (a route config
# error) from "file exists but has no ## Inject content" (an empty section).
MISSING_DOC = "\x00MISSING"


def extract_inject(doc_path: Path) -> str:
    """Return the body of the `## Inject` section only (not `## Reference`).

    Returns ``MISSING_DOC`` when the file does not exist or is unreadable (a
    broken route, distinct from a present-but-empty section which returns '').
    """
    try:
        text = doc_path.read_text()
    except OSError:
        return MISSING_DOC
    lines = text.splitlines()
    out: list[str] = []
    capturing = False
    for line in lines:
        if line.strip().startswith("## "):
            capturing = line.strip() == INJECT_HEADING
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def load_cold_cap(context_dir: Path) -> int:
    """Read `budget.max_cold_surface_per_edit` from routes.yaml, else default.

    Mirrors how `load_routes` reads `max_docs_per_edit` so all breadth tuning
    lives in one config surface. Fail-soft (spec §1): any problem — missing
    file, parse error, missing/malformed key — returns the module default
    COLD_SURFACE_CAP rather than raising.
    """
    try:
        data = yaml.safe_load((context_dir / ROUTES_FILE).read_text()) or {}
        budget = data.get("budget", {}) or {}
        if "max_cold_surface_per_edit" not in budget:
            return COLD_SURFACE_CAP
        return int(budget.get("max_cold_surface_per_edit"))
    except (OSError, yaml.YAMLError, TypeError, ValueError, AttributeError):
        return COLD_SURFACE_CAP


def _surface_cold_lines(target: str, root: Path) -> list[str]:
    """Cold one-line surfacing for `target`, fully isolated (spec §1, §2.3).

    Imports the cold module lazily (it imports `_glob_to_regex` from here, so a
    lazy import avoids a circular import at module load) and double-wraps the
    call so the engine NEVER raises to the hook. Returns [] on any failure.
    """
    try:
        cap = load_cold_cap(root / ".context")
        try:  # standalone load (run as a script) — match the test harness shape
            import cold as _cold  # noqa: PLC0415
        except ImportError:
            import importlib.util

            _cold_path = Path(__file__).resolve().parent / "cold.py"
            _spec = importlib.util.spec_from_file_location("cl_cold", _cold_path)
            assert _spec and _spec.loader
            _cold = importlib.util.module_from_spec(_spec)
            sys.modules.setdefault("cl_cold", _cold)
            _spec.loader.exec_module(_cold)
        return _cold.surface_cold(
            target, root / ".context" / "knowledge", max_items=cap
        )
    except Exception:
        return []



def _cold_slug_from_line(line: str) -> str | None:
    """Extract the leading ``[<slug>]`` citation token from a surfaced cold line.

    The cold emitter (`cold.surface_cold`) prefixes every real item line with a
    machine-parseable ``[<slug>]`` handle (D3 attribution scheme A). Return the
    slug, or None for a line that carries no token — e.g. the
    ``...(N more cold topic(s); ...)`` truncation note, which must not be
    recorded as an injected slug. Never raises.
    """
    if not line.startswith("["):
        return None
    end = line.find("]")
    if end <= 1:
        return None
    return line[1:end]


def _log_injection_event(
    root: Path,
    target: str,
    *,
    injected: list[str],
    empty: list[str],
    missing: list[str],
    over_budget: list[str],
    cold_count: int,
    cold_slugs: list[str],
) -> None:
    """Append one JSONL line per injection to sessions/.telemetry/injection.jsonl.

    The §7a gate verdict ("KEEP") rested on a single observed edit because
    nothing recorded when routes fire; this is the instrumentation that makes
    the next re-evaluation data-driven. Telemetry lives under sessions/ (a
    dot-dir, machine-local, covered by the fleet's sessions gitignore) and is
    strictly best-effort: a telemetry failure must never affect injection
    (spec §1: the router never raises).

    `cold_slugs` additionally records the slug of each surfaced cold item with a
    per-slug injection timestamp (D3 attribution scheme A substrate): the ledger
    of which cold memories were put in front of an agent, and when, so a later
    ``Context-Used: <slug>`` citation can be matched to a real injection. This is
    purely additive — the legacy ``cold_surfaced`` count and every existing field
    are unchanged, so existing telemetry consumers keep working.
    """
    try:
        tel_dir = root / ".context" / "sessions" / ".telemetry"
        tel_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat(timespec="seconds")
        event = {
            "ts": ts,
            "target": target,
            "injected": injected,
            "empty": empty,
            "missing": missing,
            "over_budget": over_budget,
            "cold_surfaced": cold_count,
            "cold_slugs": [{"slug": slug, "ts": ts} for slug in cold_slugs],
        }
        with (tel_dir / "injection.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass  # never let telemetry break the router


def build_context(target: str, root: Path) -> str:
    """Top-level: produce the injectable context block for `target`, or ''.

    Assembles the warm leaf-doc conventions (existing behaviour) and ALSO
    appends a bounded one-line index of matching cold topics (spec §2.3 "cold
    must not be write-only"). Cold surfacing is additive: it surfaces even when
    warm produced nothing, so cold is not gated on a warm match.
    """
    # Engine code lives in .context/.engine/; routes.yaml lives in .context/ (spec §1).
    blocks: list[str] = []
    notes: list[str] = []
    injected_docs: list[str] = []
    empty: list[str] = []
    missing: list[str] = []
    over_budget: list[str] = []
    routes, max_docs, compatible = load_routes(root / ".context")
    if compatible and routes:
        docs, over_budget = select_docs_split(target, routes, max_docs)

        for doc in docs:
            body = extract_inject(root / doc)
            if body == MISSING_DOC:
                missing.append(doc)
            elif body:
                blocks.append(f"<!-- {doc} -->\n{body}")
                injected_docs.append(doc)
            else:
                empty.append(doc)

        # Diagnostics — surfaced even when every matched doc was unusable, so a
        # fully broken route set is distinguishable from a no-match. Spec §3: no
        # silent truncation.
        if empty:
            notes.append(f"[router: {len(empty)} matched doc(s) had no ## Inject section]")
        if missing:
            notes.append(
                f"[router: {len(missing)} matched doc(s) not found (broken route): "
                f"{', '.join(missing)}]"
            )
        if over_budget:
            notes.append(
                f"[router: dropped {len(over_budget)} over-budget doc(s): "
                f"{', '.join(over_budget)}]"
            )

    # Cold one-line surfacing — same pass, isolated, never raises (spec §1).
    cold_lines = _surface_cold_lines(target, root)

    # Early return: only '' when warm blocks AND notes AND cold are all empty.
    # Otherwise cold can surface on a target that has no warm route at all (the
    # common case: most code has cold knowledge but no leaf doc) — spec §2.3.
    if not blocks and not notes and not cold_lines:
        return ""

    # Recover the citation slug from each surfaced cold line (skipping the
    # ...(N more) truncation note, which carries no token) so the telemetry can
    # record exactly which cold items were injected — the D3 attribution substrate.
    cold_slugs = [
        slug for line in cold_lines if (slug := _cold_slug_from_line(line)) is not None
    ]

    # Something is being surfaced — record it (best-effort, never raises).
    _log_injection_event(
        root,
        target,
        injected=injected_docs,
        empty=empty,
        missing=missing,
        over_budget=list(over_budget),
        cold_count=len(cold_lines),
        cold_slugs=cold_slugs,
    )

    note = ("\n\n" + "\n".join(notes)) if notes else ""
    cold_section = ""
    if cold_lines:
        cold_section = (
            "\n\nRelated cold topics (pull on demand):\n" + "\n".join(cold_lines)
        )
        # Self-describing block (D3 P0-B): when at least one REAL cold item is
        # injected (a slug-bearing line, not just the truncation note), close
        # the block with the citation instruction so the acting agent knows the
        # `Context-Used:` trailer protocol. Rendered-only: appended after the
        # telemetry above was assembled, so it never counts as a surfaced line
        # and never appears in cold_slugs.
        if cold_slugs:
            cold_section += "\n" + COLD_CITATION_NOTE

    if not blocks:
        # No injectable warm docs. Emit any diagnostics + cold section.
        warm_part = note.lstrip("\n") if notes else ""
        return (warm_part + cold_section).lstrip("\n")

    header = f"Relevant conventions for `{target}` (injected before edit):"
    return header + "\n\n" + "\n\n".join(blocks) + note + cold_section


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Context-injection router")
    parser.add_argument("--target", required=True, help="repo-relative file path being edited")
    parser.add_argument("--root", default=".", help="repo root (CL_ANCHOR)")
    args = parser.parse_args(argv)
    try:
        block = build_context(args.target, Path(args.root))
    except Exception:  # never blocks a hook: any failure → no output, exit 0
        return 0
    if block:
        sys.stdout.write(block + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
