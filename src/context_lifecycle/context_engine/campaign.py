# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Campaign front-matter parser (PROTOTYPE) — spec §2.2b / §2.3.

`.console/task.md` is the consolidation BOUNDARY. Per §2.2b the file gains a
leading YAML front-matter block carrying `campaign_id` (e.g.
``c-2026-06-03-XXXX``) and `status` (active|done). A campaign_id change — or,
for the still-freeform current file, an Objective overwrite — is the detectable
successor to today's overwrite-in-place, and is what gates the §2.3 trigger.

This module reuses cold._split_frontmatter and cold._extract_section EXACTLY
(imported via the same standalone-load shim cold.py uses for route.py) so the
parsing semantics are identical across the engine. It NEVER raises (spec §1):
an unreadable/missing task.md degrades to ``parse_task -> None`` and
``boundary_changed -> False``.

The live ``.console/task.md`` may carry the §2.2b front-matter (the additive
template edit) or be a plain template; both parse cleanly. A plain template
yields ``Campaign(campaign_id=None, status='active', started=None,
objective=<## Objective text>)``.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path


def _load_cold():
    """Import the cold module, package or standalone (mirrors route._surface)."""
    try:  # pragma: no cover - import shim
        import cold as _cold  # noqa: PLC0415

        return _cold
    except ImportError:  # pragma: no cover
        _cold_path = Path(__file__).resolve().parent / "cold.py"
        _spec = importlib.util.spec_from_file_location("cl_cold", _cold_path)
        assert _spec and _spec.loader
        _cold = importlib.util.module_from_spec(_spec)
        sys.modules.setdefault("cl_cold", _cold)
        _spec.loader.exec_module(_cold)
        return _cold


OBJECTIVE_HEADING = "## Objective"
DEFAULT_STATUS = "active"


@dataclass(frozen=True)
class Campaign:
    """A parsed §2.2b campaign boundary descriptor.

    ``campaign_id`` is None when the task.md has no front-matter (the current
    plain template). ``status`` defaults to 'active'. ``objective`` is the text
    of the ``## Objective`` section (used for the front-matter-less fallback in
    :func:`boundary_changed`).
    """

    campaign_id: str | None
    status: str
    started: str | None
    objective: str


def parse_task(task_path: Path) -> Campaign | None:
    """Parse ``.console/task.md`` into a Campaign, or None on unreadable file.

    Fail-soft (spec §1): missing/unreadable file -> None; never raises.
    Front-matter is parsed with cold._split_frontmatter so a malformed block
    degrades to ``({}, body)`` and the file still parses as a plain template.
    """
    try:
        text = task_path.read_text()
    except (OSError, UnicodeDecodeError):
        return None

    try:
        cold = _load_cold()
        fm, body = cold._split_frontmatter(text)

        objective = cold._extract_section(body, OBJECTIVE_HEADING)

        campaign_id = fm.get("campaign_id") if isinstance(fm, dict) else None
        if campaign_id is not None:
            campaign_id = str(campaign_id) or None

        status = fm.get("status") if isinstance(fm, dict) else None
        if not isinstance(status, str) or not status:
            status = DEFAULT_STATUS

        started = fm.get("started") if isinstance(fm, dict) else None
        if started is not None:
            started = str(started)

        return Campaign(
            campaign_id=campaign_id,
            status=status,
            started=started,
            objective=objective,
        )
    except Exception:
        return None


def objective_hash(objective: str) -> str:
    """Stable short hash of the Objective text — the fallback boundary signal.

    Used when the task.md has no front-matter: a changed Objective hash is the
    detectable successor to overwrite-in-place (§2.2b/§2.3).
    """
    return hashlib.sha256(objective.strip().encode("utf-8")).hexdigest()[:16]


def boundary_changed(
    task_path: Path,
    last_seen_campaign_id: str | None = None,
    last_seen_objective_hash: str | None = None,
) -> bool:
    """True when a campaign boundary changed since the recorded last-seen state.

    Three detectable signals (spec §2.2b/§2.3), in precedence order:
      (a) formalized: parsed ``campaign_id`` differs from ``last_seen_campaign_id``;
      (b) close: ``status`` flipped to 'done';
      (c) fallback (front-matter ABSENT): the ``## Objective`` text hash changed
          vs ``last_seen_objective_hash`` — the successor to overwrite-in-place.

    Returns False on an unreadable task.md (treated as no boundary) — never
    raises (spec §1).
    """
    campaign = parse_task(task_path)
    if campaign is None:
        return False

    # (b) status flip to done is always a boundary (campaign closed).
    if campaign.status == "done":
        return True

    # (a) formalized path: a campaign_id is present and differs.
    if campaign.campaign_id is not None:
        return campaign.campaign_id != last_seen_campaign_id

    # (c) fallback: no front-matter campaign_id — compare Objective hash.
    if last_seen_objective_hash is None:
        # No prior record to compare against: treat as no boundary so a first
        # observation does not spuriously fire the trigger.
        return False
    return objective_hash(campaign.objective) != last_seen_objective_hash
