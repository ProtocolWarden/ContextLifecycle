# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Age-based retention for ephemeral session state (`cl session prune`).

Session subdirs under ``<anchor>/.context/sessions/`` are the ephemeral tier:
the design invariant is that **a session file must never hold the only copy of
anything worth keeping** (durable knowledge lives in `.console/` truth, warm
leaf docs, or the cold store). Loop controllers and executor backends that
never call ``cl session end`` leave their subdirs (and per-dispatch ``l-*.yaml``
lease records) behind forever — tens of thousands of files on a busy host.

This module deletes session subdirs older than a cutoff. Dry-run by default
(mirrors ``cl reconcile prune``); ``--apply`` mutates. The session named by
``$CL_SESSION_ID`` is always retained regardless of age.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from context_lifecycle.session.paths import archived_root as archived_root_for

DEFAULT_RETAIN_DAYS = 14

# Session ids are date-stamped: s-YYYY-MM-DD-xxxx (ids.generate_session_id).
_SESSION_DATE_RE = re.compile(r"^s-(\d{4}-\d{2}-\d{2})-")


@dataclass
class PruneCandidate:
    path: Path
    session_id: str
    session_date: date | None
    file_count: int
    bytes_total: int


@dataclass
class SessionPrunePlan:
    anchor: Path
    cutoff: date
    candidates: list[PruneCandidate] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    applied: bool = False

    @property
    def file_count(self) -> int:
        return sum(c.file_count for c in self.candidates)

    @property
    def bytes_total(self) -> int:
        return sum(c.bytes_total for c in self.candidates)


def _session_date(dirname: str, dir_path: Path) -> date | None:
    """Date a session dir: parse the id's date stamp, fall back to mtime."""
    m = _SESSION_DATE_RE.match(dirname)
    if m:
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(dir_path.stat().st_mtime).date()
    except OSError:
        return None


def _measure(root: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for p in root.rglob("*"):
        if p.is_file():
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                continue
    return files, size


def build_session_prune_plan(
    anchor: Path,
    *,
    retain_days: int = DEFAULT_RETAIN_DAYS,
    include_archived: bool = False,
    current_session_id: str | None = None,
    today: date | None = None,
) -> SessionPrunePlan:
    """Plan a session prune (no mutation).

    A session dir is a candidate when its date (id stamp, else mtime) is
    strictly older than ``today - retain_days``. The current session and
    undatable dirs are always kept.
    """
    anchor = Path(anchor)
    today = today or date.today()
    cutoff = today - timedelta(days=retain_days)
    plan = SessionPrunePlan(anchor=anchor, cutoff=cutoff)

    roots = [anchor / ".context" / "sessions"]
    if include_archived:
        roots.append(archived_root_for(anchor))

    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            sid = child.name
            if current_session_id and sid == current_session_id:
                plan.kept.append(sid)
                continue
            d = _session_date(sid, child)
            if d is None or d >= cutoff:
                plan.kept.append(sid)
                continue
            files, size = _measure(child)
            plan.candidates.append(
                PruneCandidate(
                    path=child,
                    session_id=sid,
                    session_date=d,
                    file_count=files,
                    bytes_total=size,
                )
            )
    return plan


def apply_session_prune(plan: SessionPrunePlan) -> SessionPrunePlan:
    """Delete every candidate dir. Idempotent (a vanished dir is skipped)."""
    for cand in plan.candidates:
        if cand.path.is_dir():
            shutil.rmtree(cand.path, ignore_errors=True)
    plan.applied = True
    return plan


def format_session_prune_plan(plan: SessionPrunePlan, *, applied: bool) -> str:
    verb = "applied" if applied else "dry-run (no changes written)"
    mib = plan.bytes_total / (1024 * 1024)
    lines = [
        f"session prune {plan.anchor} — {verb}; cutoff {plan.cutoff}: "
        f"{len(plan.candidates)} session(s), {plan.file_count} file(s), {mib:.1f} MiB"
    ]
    for c in plan.candidates:
        lines.append(
            f"  - {c.session_id} ({c.session_date}; {c.file_count} files)"
        )
    if not plan.candidates:
        lines.append("  nothing to prune.")
    return "\n".join(lines)
