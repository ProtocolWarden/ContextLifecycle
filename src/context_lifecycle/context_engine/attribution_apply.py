# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""D3 P3 — two-phase, write-once consequence WRITER (human ``--apply`` only).

Dispatches a P2 :class:`attribution.AttributionPlan` into cold-item
frontmatter via ``dataclasses.replace`` + ``cold.write_item`` (the single
writer — every write is an atomic whole-file rewrite). This module runs ONLY
inside the reviewed ``consolidate.py --apply`` boundary (spec §4.5): the human
interlock stands; there is deliberately NO CLI here — a standalone entrypoint
would be a second, unreviewed mutation surface. Autonomous apply is P4, an
explicit operator decision, NOT this module.

Two phases (spec §3.5), both MONOTONE and re-checked at write time (the plan
may be stale — disk is re-read per item, never trusted from plan time):

  **Phase A — new attributions.** For each planned ``(slug, sha, tests_green)``:
  re-load the item fresh from disk; skip (with a recorded reason) unless it is
  still cold-tier AND ``acted_on_commit`` is not already a real sha (§4.4
  write-once, enforced at write time, not just plan time). Then write
  ``consequence.acted_on_commit=<sha>`` and ``consequence.tests_green`` EXACTLY
  as planned — verbatim ``True | False | "unknown"``; a planned ``"unknown"``
  is written as ``"unknown"``, never upgraded, and any nonstandard planned
  value degrades to ``"unknown"``, NEVER to ``True``.

  **Phase B — CI flip for attributed items.** For each cold item on disk with
  a real ``acted_on_commit`` sha AND ``tests_green == "unknown"`` (including
  items Phase A just wrote — an immediate CI verdict is just an early §3.5
  "arrival"): resolve CI for THAT sha through an injectable seam (default:
  ``attribution._default_resolve_tests_green`` over ``ci_status``, fail-closed).
  Only a literal ``True`` / ``False`` is ever written; anything else leaves the
  item untouched. The transition is ``"unknown" -> True|False`` at most once:
  ``True``/``False`` items never enter the worklist and are re-checked before
  the write, so NEVER ``True -> anything``, NEVER ``False -> anything``, never
  a re-flip. No path defaults to ``True``.

Token/repo identity for Phase B in the human-run CLI: NO secret on argv and NO
environment reads. ``token`` comes from an injectable provider whose default
shells to ``gh auth token`` (gh is this repo's auth source of truth —
``ci_status`` already queries through ``gh``); ``github_repo`` from a provider
that parses ``git remote get-url origin`` (https/ssh/scp forms). Both are
bounded subprocesses; ANY failure ⇒ ``None`` ⇒ the CI seam resolves
``"unknown"`` ⇒ Phase B leaves items inert (safe — ``"unknown"`` still fails
the promotion gate).

Guarantees (the house apply idiom, ``consolidate.plan_consolidation``): the
apply path may write but NEVER raises to the caller — every per-item failure
is recorded and the batch continues; the returned :class:`AppliedResult` lists
applied / flipped / skipped with machine-readable reasons (auditable).

``run_attribution`` is the one integration entrypoint ``consolidate`` calls:
dry-run computes the plan + the pending Phase-B worklist for HUMAN visibility
(zero writes); ``apply=True`` runs both phases. Fail-soft throughout — in a
scaffolded consumer where the attribution modules are absent, everything
degrades to "no attribution", never breaking the consolidation pass.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

# The verbatim sentinel cold.py stores when CI cannot be resolved (matches
# ci_status.UNKNOWN / attribution.UNKNOWN; duplicated so this module loads
# standalone next to cold/route).
UNKNOWN = "unknown"

# Machine-readable skip reasons (write-time re-checks + per-item failures).
SKIP_ITEM_MISSING = "item_missing"
SKIP_NOT_COLD_TIER = "not_cold_tier"
SKIP_ALREADY_ATTRIBUTED = "already_attributed"
SKIP_ALREADY_FLIPPED = "already_flipped"
SKIP_WRITE_FAILED = "write_failed"
SKIP_CI_UNRESOLVED = "ci_unresolved"
SKIP_FLIP_FAILED = "flip_failed"

# Bounded so a slow git/gh never hangs the human's --apply run (mirrors
# attribution._GIT_TIMEOUT_SECONDS).
_SUBPROCESS_TIMEOUT_SECONDS = 30

# The same sha shape-check the promotion gate applies (consolidate._is_real_sha).
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")

# origin URL forms -> owner/repo: https://host/owner/repo(.git),
# ssh://git@host/owner/repo(.git), git@host:owner/repo(.git).
_REMOTE_RE = re.compile(
    r"^(?:[a-z+]+://[^/]+/|[^/@]+@[^/:]+:)([^/:]+)/([^/]+?)(?:\.git)?/?$"
)


# The sibling-module loader is REUSED from attribution.py, not cloned (the
# dual-mode import-shim body is identical by necessity and custodian D11
# rightly flags copies): package/standalone import first, file-location
# fallback — the same dual-mode discipline as cold.py's route import.
try:  # pragma: no cover - import shim
    from attribution import _load
except ImportError:  # pragma: no cover
    _p = Path(__file__).resolve().parent / "attribution.py"
    _spec = importlib.util.spec_from_file_location("cl_attribution", _p)
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules.setdefault("cl_attribution", _mod)
    _spec.loader.exec_module(_mod)
    _load = _mod._load


# ---- Result shapes — mirror attribution.AttributionPlan's audit posture. ---- #


@dataclass(frozen=True)
class AppliedWrite:
    """One consequence write that happened: Phase A (``applied``) or Phase B
    (``flipped``). ``tests_green`` is the verbatim value now on disk.
    """

    slug: str
    sha: str
    tests_green: object


@dataclass(frozen=True)
class SkippedWrite:
    """One planned/pending write that did NOT happen, with the reason code."""

    slug: str
    reason: str


@dataclass(frozen=True)
class PendingFlip:
    """One Phase-B worklist entry: a cold item already carrying a real
    ``acted_on_commit`` sha whose ``tests_green`` is still ``"unknown"``.
    """

    slug: str
    sha: str


@dataclass(frozen=True)
class AppliedResult:
    applied: list[AppliedWrite] = field(default_factory=list)
    flipped: list[AppliedWrite] = field(default_factory=list)
    skipped: list[SkippedWrite] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.applied and not self.flipped and not self.skipped


@dataclass(frozen=True)
class AttributionOutcome:
    """What ``consolidate`` renders/carries: the P2 plan (None when the planner
    is unavailable/failed), the apply result (None on dry-run), and the
    Phase-B worklist that remains pending on disk AFTER any writes.
    """

    plan: object | None = None
    applied: object | None = None
    pending: list[PendingFlip] = field(default_factory=list)


# ---- Injectable identity providers (no argv secrets, no env reads). -------- #


def _default_token_provider() -> str | None:
    """Obtain a GitHub token from ``gh auth token`` (bounded subprocess).

    ``gh`` is the auth source of truth in this repo (ci_status queries through
    it); this keeps the secret OFF argv and out of any env read of our own.
    Any failure — gh missing, not logged in, timeout — ⇒ None (Phase B stays
    safe-inert at ``"unknown"``).
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        token = proc.stdout.strip()
        if proc.returncode != 0 or not token:
            return None
        return token
    except Exception:
        return None


def _parse_remote(url: str) -> str | None:
    """Parse an origin URL to ``owner/repo`` (https / ssh:// / scp-style), or
    None on any unrecognized shape — fail-closed, never a guess.
    """
    m = _REMOTE_RE.match(url.strip())
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def _default_repo_provider(root: Path) -> str | None:
    """Derive ``owner/repo`` from ``git remote get-url origin`` in ``root``
    (bounded subprocess). Any failure ⇒ None ⇒ CI resolves ``"unknown"``.
    """
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["git", "remote", "get-url", "origin"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        )
        if proc.returncode != 0:
            return None
        return _parse_remote(proc.stdout.strip())
    except Exception:
        return None


def _default_resolve_tests_green(
    github_repo: str | None, sha: str, token: str | None
) -> object:
    """Default CI seam: delegate to the P2 planner's fail-closed resolver
    (``ci_status`` underneath). Either identity absent ⇒ ``"unknown"``.
    """
    attribution = _load("attribution")
    return attribution._default_resolve_tests_green(github_repo, sha, token)


# ---- Pure readers. ---------------------------------------------------------- #


def _real_sha(value: object) -> str | None:
    sha = str(value or "").strip()
    return sha if _SHA_RE.match(sha) else None


def pending_flips(root: Path) -> list[PendingFlip]:
    """The Phase-B worklist, read fresh from disk: cold-tier items whose
    ``acted_on_commit`` is a real sha and whose ``tests_green`` is still the
    verbatim ``"unknown"``. ``True``/``False`` items never appear here — that
    exclusion IS the first monotonicity guard (never re-flip a resolved value).
    Pure reader; [] on any failure.
    """
    try:
        cold = _load("cold")
        out: list[PendingFlip] = []
        for item in cold.load_index(root / ".context" / "knowledge"):
            if item.tier != "cold" or item.tests_green != UNKNOWN:
                continue
            sha = _real_sha(item.acted_on_commit)
            if sha is None:
                continue
            out.append(PendingFlip(item.slug, sha))
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# The writer.                                                                  #
# --------------------------------------------------------------------------- #


def apply_attribution(
    root: Path,
    plan,
    *,
    github_repo: str | None = None,
    token: str | None = None,
    repo_provider: Callable[[Path], str | None] = _default_repo_provider,
    token_provider: Callable[[], str | None] = _default_token_provider,
    resolve_tests_green: Callable[
        [str | None, str, str | None], object
    ] = _default_resolve_tests_green,
) -> AppliedResult:
    """Dispatch ``plan`` (Phase A), then flip resolvable pending items
    (Phase B). MUTATES cold-item files — call this ONLY from inside the
    reviewed ``--apply`` boundary. NEVER raises: every per-item failure is
    recorded in ``skipped`` and the batch continues.

    ``github_repo``/``token`` may be supplied directly (tests, wired callers);
    when None they are resolved lazily through the injectable providers — and
    only if there is at least one pending flip, so a plain Phase-A-only pass
    never spawns a subprocess.
    """
    try:
        cold = _load("cold")
        knowledge_dir = root / ".context" / "knowledge"
        applied: list[AppliedWrite] = []
        flipped: list[AppliedWrite] = []
        skipped: list[SkippedWrite] = []

        # ---- Phase A: planned attributions, re-checked at write time. ----
        for planned in getattr(plan, "attributions", None) or []:
            slug = planned.slug
            try:
                item = cold.parse_item(knowledge_dir / f"{slug}.md")
                if item is None:
                    skipped.append(SkippedWrite(slug, SKIP_ITEM_MISSING))
                    continue
                if item.tier != "cold":
                    skipped.append(SkippedWrite(slug, SKIP_NOT_COLD_TIER))
                    continue
                if _real_sha(item.acted_on_commit) is not None:
                    skipped.append(SkippedWrite(slug, SKIP_ALREADY_ATTRIBUTED))
                    continue
                tests_green = planned.tests_green
                # Verbatim contract: True | False | "unknown" only; a planned
                # "unknown" is written as "unknown" (never upgraded here — the
                # flip is Phase B's job); nonstandard degrades to "unknown".
                if tests_green is not True and tests_green is not False:
                    tests_green = UNKNOWN
                cold.write_item(
                    knowledge_dir,
                    replace(
                        item,
                        acted_on_commit=planned.sha,
                        tests_green=tests_green,
                    ),
                )
                applied.append(AppliedWrite(slug, planned.sha, tests_green))
            except Exception:
                skipped.append(SkippedWrite(slug, SKIP_WRITE_FAILED))
                continue

        # ---- Phase B: CI flips for attributed-but-unknown items. ----
        worklist = pending_flips(root)
        if worklist:
            if github_repo is None:
                try:
                    github_repo = repo_provider(root)
                except Exception:
                    github_repo = None
            if token is None:
                try:
                    token = token_provider()
                except Exception:
                    token = None
        for pending in worklist:
            try:
                # Monotone, re-checked at write time: only a fresh, still-cold,
                # still-"unknown", still-same-shaped item is ever flipped.
                item = cold.parse_item(knowledge_dir / f"{pending.slug}.md")
                if item is None:
                    skipped.append(SkippedWrite(pending.slug, SKIP_ITEM_MISSING))
                    continue
                if item.tier != "cold":
                    skipped.append(SkippedWrite(pending.slug, SKIP_NOT_COLD_TIER))
                    continue
                if item.tests_green != UNKNOWN:
                    skipped.append(SkippedWrite(pending.slug, SKIP_ALREADY_FLIPPED))
                    continue
                sha = _real_sha(item.acted_on_commit)
                if sha is None:
                    skipped.append(SkippedWrite(pending.slug, SKIP_CI_UNRESOLVED))
                    continue
                verdict = resolve_tests_green(github_repo, sha, token)
                # Literal True/False ONLY ever gets written; "unknown" or any
                # nonstandard value leaves the item untouched (never default
                # True — §3.5).
                if verdict is not True and verdict is not False:
                    skipped.append(SkippedWrite(pending.slug, SKIP_CI_UNRESOLVED))
                    continue
                cold.write_item(knowledge_dir, replace(item, tests_green=verdict))
                flipped.append(AppliedWrite(pending.slug, sha, verdict))
            except Exception:
                skipped.append(SkippedWrite(pending.slug, SKIP_FLIP_FAILED))
                continue

        return AppliedResult(applied=applied, flipped=flipped, skipped=skipped)
    except Exception:
        # Belt-and-suspenders: the apply path never raises to its caller.
        return AppliedResult()


# --------------------------------------------------------------------------- #
# The consolidate integration entrypoint + renderer.                           #
# --------------------------------------------------------------------------- #


def run_attribution(
    root: Path,
    *,
    apply: bool = False,
    default_branch: str = "main",
    plan_fn: Callable[[Path], object] | None = None,
    apply_fn: Callable[[Path, object], AppliedResult] | None = None,
) -> AttributionOutcome:
    """The one call ``consolidate`` makes, in BOTH modes.

    Dry-run (``apply=False``): compute the P2 plan + the pending Phase-B
    worklist for human review — ZERO writes. ``apply=True``: dispatch the plan
    through :func:`apply_attribution` (both phases), then report what remains
    pending. The plan is always computed WITHOUT a token (``tests_green``
    plans as ``"unknown"``): CI resolution is Phase B's job under the gh-auth
    seam, so the reviewed dry-run plan and the applied Phase-A writes match
    verbatim. Never raises; total failure ⇒ an empty outcome.
    """
    try:
        if plan_fn is None:
            attribution = _load("attribution")

            def plan_fn(r: Path):
                # No token/repo on the plan path: a secret does not belong on
                # argv (the P2 CLI choice) and Phase A writes verbatim-as-
                # reviewed; Phase B owns CI.
                return attribution.plan_attribution(
                    r, default_branch=default_branch, github_repo=None, token=None
                )

        try:
            plan = plan_fn(root)
        except Exception:
            plan = None

        applied = None
        if apply and plan is not None:
            applied = (apply_fn or apply_attribution)(root, plan)

        return AttributionOutcome(
            plan=plan, applied=applied, pending=pending_flips(root)
        )
    except Exception:
        return AttributionOutcome()


def render_outcome(outcome: AttributionOutcome) -> str:
    """Render an outcome for the reviewed plan output (consolidate._render).

    Dry-run visibility is the point: the human sees exactly what ``--apply``
    would write (the plan) plus which attributed items are awaiting a CI flip.
    Returns '' when there is nothing attribution-related to show, so the
    existing consolidation output is unchanged. Never raises.
    """
    try:
        lines: list[str] = []
        plan = outcome.plan
        if plan is not None and not plan.is_empty():
            attribution = _load("attribution")
            lines.append(attribution._render(plan))
        for pending in outcome.pending:
            lines.append(f"PENDING-CI-FLIP {pending.slug} @ {pending.sha}")
        result = outcome.applied
        if result is not None:
            for write in result.applied:
                lines.append(
                    f"ATTRIBUTED {write.slug} -> {write.sha} "
                    f"(tests_green={write.tests_green!r})"
                )
            for write in result.flipped:
                lines.append(
                    f"FLIPPED {write.slug} tests_green={write.tests_green!r} "
                    f"@ {write.sha}"
                )
            for skip in result.skipped:
                lines.append(f"  skip {skip.slug} ({skip.reason})")
        return "\n".join(lines)
    except Exception:
        return ""
