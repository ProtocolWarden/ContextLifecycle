# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Cold-store data layer (PROTOTYPE).

The entire cold-tier read/write/validate path plus the one-line surfacing
helper the router calls. Implements the §2.6 item format
(docs/architecture/context-injection-spec.md) and the §2.3 "cold must not be
write-only" surfacing rule.

State lives in `.context/knowledge/<slug>.md` (committed); logic lives here in
`.context/.engine/` (spec §1). Pure stdlib + PyYAML, mirroring route.py:

  - frozen dataclass for the parsed item
  - the build/surface entrypoints NEVER raise to their caller; a malformed item
    degrades to skipped (parse_item -> None) so the engine can never block a
    hook (spec §1).

`validate_item` is the one function that *reports* problems (for tests and the
future `cl seal` path); surfacing/loading just skip Nones.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# Glob matching is shared with the warm router so cold/warm matching semantics
# are identical (** subtree, * within-segment, anchored). Imported, not
# duplicated. This module is loaded next to route.py in the engine dir.
try:  # pragma: no cover - import shim for both package + standalone load
    from route import _glob_to_regex
except ImportError:  # pragma: no cover
    import importlib.util
    import sys as _sys

    _route_path = Path(__file__).resolve().parent / "route.py"
    _spec = importlib.util.spec_from_file_location("cl_route", _route_path)
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    _sys.modules.setdefault("cl_route", _mod)
    _spec.loader.exec_module(_mod)
    _glob_to_regex = _mod._glob_to_regex


VALID_TIERS = ("cold", "warm", "hot")
README = "README.md"

FINDING_HEADING = "## Finding"
DETAIL_HEADING = "## Detail"


@dataclass(frozen=True)
class ColdItem:
    """A parsed §2.6 cold-store item.

    `finding` is the first non-empty line of the `## Finding` section — the
    one-line surface text the router emits. `paths` are repo-relative globs.
    """

    slug: str
    topic: str
    paths: tuple[str, ...]
    created: str
    campaign_id: str
    acted_on_commit: str | None
    tests_green: object  # true | false | "unknown" — stored verbatim, not coerced
    tier: str
    pinned: bool
    last_injected: str | None
    finding: str


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a leading ``---\\n...\\n---`` YAML block from the markdown body.

    Returns ``({}, text)`` when no well-formed frontmatter is present, so a
    malformed item degrades to skipped rather than raising. A YAML parse error
    inside the block also degrades to ``({}, body)``.
    """
    if not text.startswith("---"):
        return {}, text
    # The opening fence must be a line on its own ("---" then newline).
    rest = text[3:]
    if not rest.startswith("\n"):
        return {}, text
    rest = rest[1:]
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    fm_text = rest[:end]
    # Body starts after the closing fence line.
    after = rest[end + 4 :]
    after = after.lstrip("\n")
    try:
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError:
        return {}, after
    if not isinstance(data, dict):
        return {}, after
    return data, after


def _extract_section(body: str, heading: str) -> str:
    """Return the text of a single ``## Heading`` section (same walk shape as
    route.extract_inject): capture lines after the matching heading until the
    next ``## `` heading. Returns '' when the section is absent or empty.
    """
    out: list[str] = []
    capturing = False
    for line in body.splitlines():
        if line.strip().startswith("## "):
            capturing = line.strip() == heading
            continue
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def parse_item(path: Path) -> ColdItem | None:
    """Read one ``knowledge/*.md`` and parse it into a ColdItem, or None.

    Fail-soft (spec §1): any problem — unreadable file, missing/malformed
    frontmatter, ``paths`` not a list, bad tier, missing ``## Finding`` —
    returns None (the item is skipped). NEVER raises.
    """
    try:
        # UnicodeDecodeError (a ValueError, NOT an OSError) must also degrade to
        # None: a non-UTF-8 / binary knowledge file is exactly the "unreadable
        # file" case this function's fail-soft contract (spec §1) covers.
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None

    try:
        fm, body = _split_frontmatter(text)
        if not fm:
            return None

        paths = fm.get("paths")
        if not isinstance(paths, (list, tuple)) or not paths:
            return None
        if not all(isinstance(p, str) and p for p in paths):
            return None

        tier = fm.get("tier")
        if tier not in VALID_TIERS:
            return None

        pinned = fm.get("pinned", False)
        if not isinstance(pinned, bool):
            return None

        topic = fm.get("topic")
        if not isinstance(topic, str) or not topic:
            return None

        finding = _first_line(_extract_section(body, FINDING_HEADING))
        if not finding:
            return None

        consequence = fm.get("consequence") or {}
        if not isinstance(consequence, dict):
            consequence = {}
        acted_on_commit = consequence.get("acted_on_commit")
        if acted_on_commit is not None and not isinstance(acted_on_commit, str):
            acted_on_commit = str(acted_on_commit)
        tests_green = consequence.get("tests_green", "unknown")

        created = fm.get("created")
        created = str(created) if created is not None else ""
        campaign_id = fm.get("campaign_id")
        campaign_id = str(campaign_id) if campaign_id is not None else ""
        last_injected = fm.get("last_injected")
        if last_injected is not None:
            last_injected = str(last_injected)

        return ColdItem(
            slug=path.stem,
            topic=topic,
            paths=tuple(paths),
            created=created,
            campaign_id=campaign_id,
            acted_on_commit=acted_on_commit,
            tests_green=tests_green,
            tier=tier,
            pinned=pinned,
            last_injected=last_injected,
            finding=finding,
        )
    except Exception:
        # Belt-and-suspenders: parse_item must never raise (spec §1).
        return None


def validate_item(item_or_path: ColdItem | Path) -> list[str]:
    """Return a list of human-readable schema problems (empty == valid).

    Used by tests and the future seal path; surfacing/loading never call it
    (they simply skip Nones). When given a Path that fails to parse, the
    problems are derived from the raw frontmatter so the message is specific.
    """
    if isinstance(item_or_path, ColdItem):
        return _validate_fields(
            topic=item_or_path.topic,
            paths=item_or_path.paths,
            tier=item_or_path.tier,
            pinned=item_or_path.pinned,
            finding=item_or_path.finding,
        )

    path = item_or_path
    try:
        text = path.read_text()
    except OSError as exc:
        return [f"unreadable file: {exc}"]

    fm, body = _split_frontmatter(text)
    if not fm:
        return ["missing or malformed YAML frontmatter"]

    finding = _first_line(_extract_section(body, FINDING_HEADING))
    return _validate_fields(
        topic=fm.get("topic"),
        paths=fm.get("paths"),
        tier=fm.get("tier"),
        pinned=fm.get("pinned", False),
        finding=finding,
    )


def _validate_fields(topic, paths, tier, pinned, finding) -> list[str]:
    problems: list[str] = []
    if not isinstance(topic, str) or not topic:
        problems.append("topic: must be a non-empty string")
    if not isinstance(paths, (list, tuple)) or not paths:
        problems.append("paths: must be a non-empty list of glob strings")
    elif not all(isinstance(p, str) and p for p in paths):
        problems.append("paths: every entry must be a non-empty string")
    if tier not in VALID_TIERS:
        problems.append(f"tier: must be one of {VALID_TIERS}, got {tier!r}")
    if not isinstance(pinned, bool):
        problems.append("pinned: must be a boolean")
    if not finding:
        problems.append("## Finding: section missing or empty")
    return problems


def load_index(knowledge_dir: Path) -> list[ColdItem]:
    """Build the cold index: glob ``knowledge/*.md`` (excluding README.md),
    parse each, drop items that fail to parse (None). This IS the grep index —
    the on-disk frontmatter is the queryable artifact (spec §7a). Returns []
    on any failure (missing dir, etc.).
    """
    try:
        items: list[ColdItem] = []
        for path in sorted(knowledge_dir.glob("*.md")):
            if path.name == README:
                continue
            # Per-item isolation: a single malformed file must drop ONLY itself,
            # not the entire index. parse_item is already fail-soft, but this
            # try/except guarantees one bad file can never blank the cold surface
            # for every other (valid, matching) item (spec §2.3). The loop-level
            # catch-all below is a backstop, not the primary skip mechanism.
            try:
                item = parse_item(path)
            except Exception:
                item = None
            if item is not None:
                items.append(item)
        return items
    except Exception:
        return []


def surface_cold(target: str, knowledge_dir: Path, max_items: int) -> list[str]:
    """One-line cold-topic surfacing for `target` (spec §2.3).

    For each cold-tier item whose any() path-glob matches `target`, emit one
    line ``'[<slug>] topic — <first matching path glob> — <finding>'`` (em-dash
    separators, spec §2.3 line 198-199 verbatim format for the human portion).
    The leading ``[<slug>]`` token is a machine-parseable citation handle (D3
    attribution scheme A): an acting agent that reads the injected line can cite
    the item back in a ``Context-Used: <slug>`` commit trailer. The token is
    additive — the human ``topic — glob — finding`` content after it is
    unchanged. Only ``tier == 'cold'`` items surface (warm/hot are
    injected/anchored elsewhere, avoiding double-surfacing). Sorted by topic for
    determinism; capped at `max_items` with a non-silent ``...(N more)`` note
    (spec §3 parity). Returns [] on any failure — fail-soft, never raises
    (spec §1).
    """
    try:
        if target.startswith("./"):
            target = target[2:]
        matched: list[tuple[str, str]] = []  # (topic, line)
        for item in load_index(knowledge_dir):
            if item.tier != "cold":
                continue
            hit_glob = None
            for glob in item.paths:
                if _glob_to_regex(glob).match(target):
                    hit_glob = glob
                    break
            if hit_glob is None:
                continue
            matched.append(
                (
                    item.topic,
                    f"[{item.slug}] {item.topic} — {hit_glob} — {item.finding}",
                )
            )

        matched.sort(key=lambda pair: pair[0])
        lines = [line for _topic, line in matched]
        if max_items > 0 and len(lines) > max_items:
            extra = len(lines) - max_items
            kept = lines[:max_items]
            kept.append(f"...({extra} more cold topic(s); query cold store)")
            return kept
        return lines
    except Exception:
        return []


def write_item(knowledge_dir: Path, item: ColdItem) -> Path:
    """Serialize a §2.6 item back to ``knowledge/<slug>.md``.

    Used by the seal path / tests. The Stop-hook capture path was CLOSED as
    superseded on 2026-06-05 (work-order phase-3 closure; draft retained at
    docs/architecture/phase3-capture-draft.sh for reference), so this is
    exercised by tests and a future ``cl seal`` step only — do not splice it
    into a live hook without reopening that decision. Returns the written path.
    """
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    fm = {
        "topic": item.topic,
        "paths": list(item.paths),
        "created": item.created,
        "campaign_id": item.campaign_id,
        "consequence": {
            "acted_on_commit": item.acted_on_commit,
            "tests_green": item.tests_green,
        },
        "tier": item.tier,
        "pinned": item.pinned,
        "last_injected": item.last_injected,
    }
    fm_text = yaml.safe_dump(fm, sort_keys=False, default_flow_style=False).strip()
    body = f"## Finding\n{item.finding}\n\n## Detail\n"
    text = f"---\n{fm_text}\n---\n{body}"
    path = knowledge_dir / f"{item.slug}.md"
    path.write_text(text)
    return path
