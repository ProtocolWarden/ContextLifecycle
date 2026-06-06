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
            if not child.is_dir() or child.name.startswith("."):
                # Dot-dirs are GC bookkeeping (sessions/.gc/), never sessions.
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


# --------------------------------------------------------------------------- #
# Opportunistic auto-GC (runs inside `cl session start`)                       #
# --------------------------------------------------------------------------- #
#
# Adversarially-reviewed design (2026-06-06): plain auto-delete is unsafe —
# a loop session whose *id* is 15+ days old can still be writing leases today,
# and the $CL_SESSION_ID guard only protects the starting process. The only
# shape that survives is two-stage move-then-delete:
#
#   Tier 1 (reversible): sessions older than MOVE_AFTER_DAYS move to
#     .context/archived/ with a `.gc-moved-at` stamp. A still-live writer
#     self-heals — its next capture recreates the sessions/ dir; the moved
#     snapshot stays recoverable.
#   Tier 2 (bounded): archived dirs whose stamp is older than
#     DELETE_AFTER_DAYS are deleted. Dirs archived by `cl session end`
#     (no stamp) fall back to id-date older than MOVE+DELETE days.
#
# Warn-only was rejected on fleet evidence: loop controllers start sessions
# with capture_output=True (stderr unread), and the phase-3 stop-hook nudge
# precedent showed warn-only hygiene is inert here.

MOVE_AFTER_DAYS = 14
DELETE_AFTER_DAYS = 30
GC_THROTTLE_HOURS = 24

_MOVED_AT_MARKER = ".gc-moved-at"


def _gc_state_dir(anchor: Path) -> Path:
    # A dot-named dir under sessions/ — covered by the fleet's
    # `.context/sessions/*/` gitignore pattern and skipped by every sweep.
    return anchor / ".context" / "sessions" / ".gc"


def _is_session_dir(child: Path) -> bool:
    return child.is_dir() and not child.name.startswith(".")


def auto_gc(
    anchor: Path,
    *,
    protect: frozenset[str] | set[str] = frozenset(),
    today: date | None = None,
) -> list[str]:
    """Run one two-stage GC sweep. Returns one human line per action taken."""
    anchor = Path(anchor)
    today = today or date.today()
    actions: list[str] = []

    sessions_root = anchor / ".context" / "sessions"
    archive_root = archived_root_for(anchor)

    # --- Tier 1: move expired sessions to archived/ (reversible) ----------
    move_cutoff = today - timedelta(days=MOVE_AFTER_DAYS)
    if sessions_root.is_dir():
        for child in sorted(sessions_root.iterdir()):
            if not _is_session_dir(child) or child.name in protect:
                continue
            d = _session_date(child.name, child)
            if d is None or d >= move_cutoff:
                continue
            archive_root.mkdir(parents=True, exist_ok=True)
            dst = archive_root / child.name
            if dst.exists():
                # id collision with an earlier archive — merge by suffix.
                n = 2
                while (archive_root / f"{child.name}.{n}").exists():
                    n += 1
                dst = archive_root / f"{child.name}.{n}"
            shutil.move(str(child), str(dst))
            (dst / _MOVED_AT_MARKER).write_text(today.isoformat() + "\n", encoding="utf-8")
            actions.append(f"moved {child.name} -> archived/ (id-date {d})")

    # --- Tier 2: delete long-archived dirs (bounded total state) ----------
    delete_cutoff = today - timedelta(days=DELETE_AFTER_DAYS)
    fallback_cutoff = today - timedelta(days=MOVE_AFTER_DAYS + DELETE_AFTER_DAYS)
    if archive_root.is_dir():
        for child in sorted(archive_root.iterdir()):
            if not _is_session_dir(child) or child.name in protect:
                continue
            marker = child / _MOVED_AT_MARKER
            expired = False
            if marker.is_file():
                try:
                    moved_at = date.fromisoformat(marker.read_text(encoding="utf-8").strip())
                    expired = moved_at < delete_cutoff
                except ValueError:
                    expired = False  # unreadable stamp: keep (fail-safe)
            else:
                # Archived via `cl session end` — no stamp; use a longer
                # id-date window equivalent to move+delete.
                d = _session_date(child.name, child)
                expired = d is not None and d < fallback_cutoff
            if expired:
                shutil.rmtree(child, ignore_errors=True)
                actions.append(f"deleted archived/{child.name}")
    return actions


def maybe_auto_gc(
    anchor: Path,
    *,
    protect: frozenset[str] | set[str] = frozenset(),
    today: date | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Throttled `auto_gc`: at most once per GC_THROTTLE_HOURS per anchor.

    Appends one line per action to ``sessions/.gc/log`` so silent deletions
    leave an audit trail. Callers must treat this as best-effort (wrap it);
    it must never make `cl session start` fail.
    """
    now = now or datetime.now()
    state = _gc_state_dir(Path(anchor))
    stamp = state / "last-run"
    if stamp.is_file():
        try:
            last = datetime.fromisoformat(stamp.read_text(encoding="utf-8").strip())
            if now - last < timedelta(hours=GC_THROTTLE_HOURS):
                return []
        except ValueError:
            pass  # corrupt stamp: run and rewrite it
    state.mkdir(parents=True, exist_ok=True)
    stamp.write_text(now.isoformat(), encoding="utf-8")

    actions = auto_gc(anchor, protect=protect, today=today)
    if actions:
        with (state / "log").open("a", encoding="utf-8") as fh:
            for a in actions:
                fh.write(f"{now.isoformat()} {a}\n")
    return actions
