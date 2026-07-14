# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Committed-truth check (C2) — the keyless analogue of Track C's anchor.

When no signed reference exists the loop has no cryptographic anchor, but the
committed history is still an authority the fleet cannot rewrite from inside a
session: ``origin/main`` is protected by branch rules + CODEOWNERS, so the
*committed* copy of the config is a trustworthy "restore" target even without a
signature. C2 makes "restore vs. change" decidable the same way Track C does,
keylessly:

- read the committed ``pseudo_operator:`` section from ``origin/main``
  (``git show origin/main:<relpath>`` after a bounded ``git fetch``);
- drift (live section != committed section) → the caller runs the COMMITTED
  copy and flags the divergence — restore-by-consumption, no YAML rewriting;
- match → the live section IS the committed truth, run it as normal unsigned.

Degrade-never-halt: an ordinary launch that cannot reach ``origin`` (offline,
fetch failure, config absent on origin, git unavailable) SKIPS the check with a
loud note rather than refusing — the keyless check is a guardrail, not a gate.
The gate form is opt-in via the launcher's ``--require-committed`` flag, which
lives outside agent-reachable config exactly like ``--require-signed``.

Signed reference present ⇒ Track C wins; C2 is not consulted (see
``config.load_verified_config``).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

from .signing import _load_section, canonical_bytes

COMMITTED_REF = "origin/main"
# Bounded so an offline/slow launch degrades quickly instead of hanging.
FETCH_TIMEOUT_SECONDS = 20
GIT_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class CommittedResult:
    """Outcome of comparing the live section against ``origin/main``."""

    status: str  # "match" | "drift" | "skip"
    detail: str
    committed: dict | None  # the committed section (match/drift), else None


def _run_git(args: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _section_from_yaml_text(text: str) -> dict:
    """Parse the committed YAML bytes to the same section Track C anchors."""
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("committed config is not a YAML mapping")
    section = data.get("pseudo_operator", data)
    if not isinstance(section, dict):
        raise ValueError("committed config has no pseudo_operator section")
    return section


def verify_committed(config_file: Path) -> CommittedResult:
    """Compare the live section against the committed copy on ``origin/main``.

    Never raises on reachability/parse failures — those return ``skip`` (the
    caller runs the live section unsigned, loudly). Only genuine drift returns
    ``drift`` with the committed section for the caller to consume.
    """
    cwd = config_file.resolve().parent
    try:
        fetch = _run_git(["fetch", "--quiet", "origin", "main"], cwd, FETCH_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return CommittedResult("skip", f"origin fetch failed: {exc}", None)
    if fetch.returncode != 0:
        return CommittedResult("skip", f"origin fetch failed: {fetch.stderr.strip()}", None)

    try:
        top = _run_git(["rev-parse", "--show-toplevel"], cwd, GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        return CommittedResult("skip", f"git rev-parse failed: {exc}", None)
    if top.returncode != 0:
        return CommittedResult("skip", f"not a git repo: {top.stderr.strip()}", None)
    try:
        relpath = config_file.resolve().relative_to(Path(top.stdout.strip()))
    except ValueError as exc:
        return CommittedResult("skip", f"config file outside git repo: {exc}", None)

    try:
        show = _run_git(
            ["show", f"{COMMITTED_REF}:{relpath.as_posix()}"], cwd, GIT_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommittedResult("skip", f"git show failed: {exc}", None)
    if show.returncode != 0:
        return CommittedResult(
            "skip", f"config absent on {COMMITTED_REF}: {show.stderr.strip()}", None
        )

    try:
        committed = _section_from_yaml_text(show.stdout)
    except ValueError as exc:
        return CommittedResult("skip", f"committed config unreadable: {exc}", None)
    try:
        live = _load_section(config_file)
    except ValueError as exc:
        return CommittedResult("skip", f"live config unreadable: {exc}", None)

    if canonical_bytes(live) != canonical_bytes(committed):
        return CommittedResult(
            "drift",
            f"live pseudo_operator section diverges from {COMMITTED_REF}",
            committed,
        )
    return CommittedResult("match", f"live section matches {COMMITTED_REF}", committed)


__all__ = [
    "COMMITTED_REF",
    "CommittedResult",
    "verify_committed",
]
