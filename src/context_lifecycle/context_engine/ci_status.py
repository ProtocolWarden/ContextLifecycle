# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Fail-closed CI-status resolver — the §9 sha → CI-outcome half of D3.

The cold-store item (`cold.py`) declares two attribution fields the D3
consequence-writer will populate: ``acted_on_commit`` (a real sha) and
``tests_green`` (``True | False | "unknown"``, stored verbatim). Today
``consolidate._is_real_sha`` only *shape-checks* the sha; it "does NOT yet
resolve the sha against the repo … the deferred §9 attribution item." This
module resolves the CI half of that item: given ``owner/repo`` and a commit
``sha``, it reports whether that commit's CI is green.

Design invariant — **fail-closed, never True on doubt.** The result feeds a
promotion gate whose only passing value is boolean ``True``; a false ``True``
would promote an unproven finding. So ``tests_green`` is ``True`` *only* when
GitHub returned ≥1 check run and every one completed cleanly. Anything
ambiguous — no runs, a still-running run, an unknown sha, a missing token, a
malformed response, an errored/timed-out query — resolves to ``"unknown"``. A
run that concluded in a failing state resolves to ``False``. The whole call is
wrapped so it NEVER raises to the caller: any exception ⇒ ``"unknown"``.

Read-only: this module only queries GitHub's check-runs API (via ``gh api``,
matching CL's subprocess idiom) and never mutates anything. No caller wires it
yet — it ships standalone and fully unit-tested; a later PR consumes it.

Rollup mirrors OperationsCenter's ``get_failed_checks`` / ``get_incomplete_checks``
(dedupe by name, keep the newest run id) so the two agree on what "green" means.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from typing import Literal

# GET /repos/{owner}/{repo}/commits/{sha}/check-runs — a single page of up to
# 100 runs (matches OC's per_page=100). A commit with >100 distinct checks is
# not a real scenario here; if it ever were, the extra runs are simply unseen,
# which can only make the result *more* conservative (never a false True).
_CHECK_RUNS_PATH = "repos/{owner}/{repo}/commits/{sha}/check-runs?per_page=100"

# Bounded so a slow/hung network degrades to "unknown" instead of blocking.
_GH_TIMEOUT_SECONDS = 30

# Terminal conclusions that mean the check did NOT pass → tests_green = False.
FAILING_CONCLUSIONS: frozenset[str] = frozenset(
    {"failure", "timed_out", "cancelled", "action_required", "stale"}
)
# Terminal conclusions that count as passing. "neutral"/"skipped" are
# non-blocking in GitHub's own combined-status rollup, so they do not sink a
# green result. Any *other* conclusion on a completed run (including a missing
# one) is treated as doubtful → "unknown", never as passing.
PASSING_CONCLUSIONS: frozenset[str] = frozenset({"success", "neutral", "skipped"})

# The verbatim sentinel cold.py stores when CI cannot be resolved.
UNKNOWN: Literal["unknown"] = "unknown"

# The value type of ``tests_green`` — exactly what cold.py keeps verbatim.
TestsGreen = bool | Literal["unknown"]


@dataclass(frozen=True)
class CIStatus:
    """Resolved CI outcome for one commit.

    ``tests_green`` is the field cold.py stores verbatim: boolean ``True`` /
    ``False``, or the string ``"unknown"`` — never any other value. ``resolved``
    is ``True`` only when GitHub returned at least one check run for the sha
    (i.e. the sha is known and had CI); it is ``False`` for every doubt path
    (no runs, missing token, error). ``conclusions`` is the deduped set of raw
    run conclusions/statuses observed, kept for auditability.
    """

    tests_green: TestsGreen
    resolved: bool
    conclusions: frozenset[str]
    detail: str


def _unknown(detail: str, conclusions: frozenset[str] = frozenset()) -> CIStatus:
    """Build the fail-closed ``"unknown"`` result with an explanatory note."""
    return CIStatus(tests_green=UNKNOWN, resolved=False, conclusions=conclusions, detail=detail)


def _run_gh(args: list[str], *, token: str, timeout: int) -> subprocess.CompletedProcess[str]:
    """Injectable network seam: invoke ``gh`` with the token in the environment.

    Isolated to a single, monkeypatchable function so tests never touch the
    network. Passing the token via ``GH_TOKEN`` lets ``gh`` authenticate the
    read without depending on ambient ``gh auth`` state.
    """
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["gh", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _fetch_check_runs(
    owner: str, repo: str, sha: str, *, token: str, timeout: int
) -> list[dict]:
    """Return the raw list of check runs for ``sha`` (may be empty).

    Raises on any failure — non-zero exit (unknown sha, auth failure, rate
    limit) or malformed JSON — so the caller's blanket handler maps it to
    ``"unknown"``. Never returns a partial/guessed result.
    """
    path = _CHECK_RUNS_PATH.format(owner=owner, repo=repo, sha=sha)
    proc = _run_gh(
        ["api", path, "-H", "Accept: application/vnd.github+json"],
        token=token,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"gh api exited {proc.returncode}: {proc.stderr.strip()[:200]}"
        )
    payload = json.loads(proc.stdout)
    runs = payload.get("check_runs", [])
    if not isinstance(runs, list):
        raise ValueError("check_runs is not a list")
    return runs


def _latest_by_name(runs: list[dict]) -> list[dict]:
    """Dedupe runs by check name, keeping the newest (highest id).

    A force-push can trigger two runs for the same check on one sha; GitHub ids
    increase monotonically, so the highest id is the current run. Mirrors OC's
    ``get_failed_checks`` dedupe so both tools judge the same set.
    """
    latest: dict[str, dict] = {}
    for cr in runs:
        name = cr.get("name", "unknown")
        if cr.get("id", 0) > latest.get(name, {}).get("id", 0):
            latest[name] = cr
    return list(latest.values())


def _rollup(runs: list[dict]) -> CIStatus:
    """Apply the fail-closed rollup to a deduped run set.

    - no runs                          → "unknown" (nothing proves green)
    - any run not ``completed``        → "unknown" (still in flight)
    - any completed run with an
      unrecognized/absent conclusion   → "unknown" (doubt)
    - any completed run failing        → False
    - every run completed & passing    → True
    """
    latest = _latest_by_name(runs)
    if not latest:
        return _unknown("no check runs for sha")

    conclusions = frozenset(
        str(cr.get("conclusion") or cr.get("status") or "unknown") for cr in latest
    )

    incomplete = False
    doubtful = False
    failed = False
    for cr in latest:
        if cr.get("status") != "completed":
            incomplete = True
            continue
        conclusion = cr.get("conclusion")
        if conclusion in FAILING_CONCLUSIONS:
            failed = True
        elif conclusion not in PASSING_CONCLUSIONS:
            # completed but conclusion is None or something unmodelled → doubt
            doubtful = True

    if failed:
        return CIStatus(
            tests_green=False, resolved=True, conclusions=conclusions, detail="a check run failed"
        )
    if incomplete:
        return _unknown("a check run has not completed", conclusions)
    if doubtful:
        return _unknown("a completed run has an unresolved conclusion", conclusions)
    return CIStatus(
        tests_green=True, resolved=True, conclusions=conclusions, detail="all check runs passed"
    )


def resolve_ci_status(
    owner: str,
    repo: str,
    sha: str,
    *,
    token: str | None = None,
    timeout: int = _GH_TIMEOUT_SECONDS,
) -> CIStatus:
    """Resolve whether commit ``sha`` in ``owner/repo`` has green CI.

    Returns a :class:`CIStatus` whose ``tests_green`` is exactly ``True`` /
    ``False`` / ``"unknown"`` — the verbatim contract cold.py stores. Read-only
    and **fail-closed**: never raises, and never reports ``True`` on any doubt.

    ``token`` is supplied by the caller (which owns its own config/env layer);
    this module never reads it from the ambient environment. With no token, no
    request is made and the result is ``"unknown"`` — a resolver that cannot
    authenticate cannot vouch for green CI.
    """
    if not token:
        return _unknown("no GitHub token supplied")
    if not (owner and repo and sha):
        return _unknown("owner, repo, and sha are all required")

    try:
        runs = _fetch_check_runs(owner, repo, sha, token=token, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — every failure is fail-closed "unknown"
        return _unknown(f"check-runs query failed: {type(exc).__name__}: {exc}"[:200])
    return _rollup(runs)


__all__ = [
    "FAILING_CONCLUSIONS",
    "PASSING_CONCLUSIONS",
    "UNKNOWN",
    "CIStatus",
    "TestsGreen",
    "resolve_ci_status",
]
