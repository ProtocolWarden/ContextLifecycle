# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Session-dir pruning (PROTOTYPE) — spec §5 / §2.3.

`.context/sessions/<sid>/` orphans accumulate because ``cl session end`` is
never called (the repo carries a backlog of orphaned dirs). This module
classifies each session dir as prunable or kept and — only when ``apply=True``
— MOVES prunable dirs to ``.context/archived/<sid>/`` (the existing archived/
convention), never ``rm -rf``, so pruning is reversible.

SAFETY-FIRST classification so the dry-run is trustworthy:
  - the active session (CL_SESSION_ID env or an explicit ``active_sid``) is
    NEVER prunable;
  - a dir is prunable only when every ``active/*.yaml`` capsule is terminal
    (status in TERMINAL_STATUSES) OR the dir has no non-.gitkeep capsules;
  - the ``sessions/`` root and ``.gitkeep`` are never touched.

Pure stdlib + PyYAML. Fail-soft (spec §1): a malformed capsule is treated as
non-terminal (kept — conservative), and any unexpected failure yields an empty
plan rather than raising.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

import yaml

# A capsule whose status is in this set is finished and contributes no reason to
# keep the session dir alive. `succeeded`/`failed`/`done`/`sealed` cover the
# observed capsule schema (status: succeeded / failed) plus the spec's terminal
# vocabulary. Anything else (e.g. running, in_progress, or absent) is treated as
# non-terminal -> the dir is KEPT.
TERMINAL_STATUSES = frozenset(
    {"done", "failed", "sealed", "succeeded", "cancelled", "canceled", "aborted"}
)

GITKEEP = ".gitkeep"
ACTIVE_SUBDIR = "active"


@dataclass(frozen=True)
class PrunePlan:
    sid: str
    path: Path
    prunable: bool
    reason: str


def _capsule_status(path: Path) -> object:
    """Read the ``status:`` field of one capsule yaml, or None on any failure.

    A malformed/unreadable capsule returns None, which the caller treats as
    non-terminal (conservative: keep the dir).
    """
    try:
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict):
            return None
        return data.get("status")
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None


def _classify(session_dir: Path, active_sid: str | None) -> PrunePlan:
    sid = session_dir.name

    if active_sid is not None and sid == active_sid:
        return PrunePlan(sid, session_dir, False, "active session, kept")

    active_dir = session_dir / ACTIVE_SUBDIR

    saw_capsule = False
    seen = 0
    if active_dir.is_dir():
        # Short-circuit: a single non-terminal capsule already makes the dir KEEP,
        # so stop on the first one instead of reading every capsule. Live session
        # dirs in this repo hold tens of thousands of capsules (s-*-6c99 ~28k,
        # s-*-7114 ~31k); a full yaml.safe_load of each would stall the dry-run
        # for ~50s and is unsafe to splice into a SessionStart hook (spec §2.3/§5).
        # iterdir() (lazy) avoids materializing the whole glob list up front.
        for cap in active_dir.iterdir():
            if cap.name == GITKEEP or cap.suffix != ".yaml" or not cap.is_file():
                continue
            saw_capsule = True
            seen += 1
            status = _capsule_status(cap)
            if not (isinstance(status, str) and status in TERMINAL_STATUSES):
                return PrunePlan(
                    sid,
                    session_dir,
                    False,
                    f"non-terminal capsule, kept (>=1 live after {seen} read)",
                )

    if not saw_capsule:
        return PrunePlan(
            sid, session_dir, True, "no active capsules, prunable (orphan)"
        )

    return PrunePlan(
        sid,
        session_dir,
        True,
        f"terminal: {seen} capsule(s) all terminal, prunable",
    )


def plan_prune(
    sessions_dir: Path,
    *,
    active_sid: str | None = None,
    keep_active: bool = True,
) -> list[PrunePlan]:
    """Classify every ``sessions/s-*/`` dir as prunable or kept (read-only).

    ``active_sid`` defaults to the ``CL_SESSION_ID`` env var when not passed and
    ``keep_active`` is True. Returns [] on any failure (missing sessions dir,
    etc.) — never raises (spec §1). Mutates NOTHING; use :func:`apply_prune`.
    """
    try:
        if active_sid is None and keep_active:
            active_sid = os.environ.get("CL_SESSION_ID") or None

        plans: list[PrunePlan] = []
        if not sessions_dir.is_dir():
            return []
        for session_dir in sorted(sessions_dir.glob("s-*")):
            if not session_dir.is_dir():
                continue
            try:
                plans.append(_classify(session_dir, active_sid))
            except Exception:
                # Per-dir isolation: a single bad dir can't abort the plan.
                plans.append(
                    PrunePlan(session_dir.name, session_dir, False, "classify error, kept")
                )
        return plans
    except Exception:
        return []


def apply_prune(plans: list[PrunePlan], archived_dir: Path) -> list[Path]:
    """MOVE each prunable session dir to ``archived/<sid>/`` (apply-only).

    Returns the list of destination paths actually moved. Per-item isolation:
    one failed move never aborts the rest (mirrors load_index). Never raises.
    """
    moved: list[Path] = []
    try:
        archived_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return moved
    for plan in plans:
        if not plan.prunable:
            continue
        try:
            dest = archived_dir / plan.sid
            if dest.exists():
                # Already archived under this sid — skip rather than clobber.
                continue
            shutil.move(str(plan.path), str(dest))
            moved.append(dest)
        except Exception:
            continue
    return moved
