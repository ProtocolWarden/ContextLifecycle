# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Self-verifying promotion — the only judgment the machine is allowed to make.

The discipline: capture adds *unjudged* candidates; the machine must never write
a judgment it cannot verify, because that manufactures false confidence. This
module carves out the one promotion that is **not** a judgment at all — it is a
*re-verification*:

    A signal earns its judgment exactly once, from a human (or a sign-off path).
    If that judgment is an encodable check, the human appends a machine-readable
    ref:  `… → **judgment:** <text> [check: <kind>:<args>]`.
    From then on, every *recurrence* of that signal is auto-promoted by
    confirming the referenced check is still live — citing the existing judgment,
    inventing nothing.

If the referenced check fails to resolve (a detector was deleted, a CI job
renamed) the promoter refuses to promote and reports it as **regressed** — an
encoded judgment rotted, which is itself a high-value signal for a human.

A *check ref* is a colon-delimited, file-read-only locator (no command
execution): the green-gate artifact either exists on disk or it does not.

  custodian:<repo>:<detector-id>      detector id present in <repo>/.custodian.yaml
  ci:<repo>:<workflow-file>:<job>     <job> present in <repo>/.github/workflows/<wf>
  path:<repo>:<relpath>               <repo>/<relpath> exists

``<repo>`` resolves under a ``repos_root`` (the directory holding the managed
checkouts). Verification is pure file I/O so the promoter never runs fleet code.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable
from pathlib import Path

from context_lifecycle.ledger.capture import LedgerUnavailable, ledger_path

# - [ ] CANDIDATE 2026-06-16 <signal> — <context> — judgment: ___
_CANDIDATE_LINE_RE = re.compile(
    r"^- \[ \] CANDIDATE (\d{4}-\d{2}-\d{2}) (\S+) — (.*?) — judgment: ___\s*$",
    re.MULTILINE,
)
# A human (or signed-off) source judgment carrying a verifiable ref. The `:` after
# the signal token and the `[check: …]` tag together mark it machine-linkable.
#   - **2026-05-01** — green-gate-override: operator merged amber → **judgment:** … [check: ci:OperationsCenter:validate.yml:custodian]
_SOURCE_RE = re.compile(
    r"^- \*\*(\d{4}-\d{2}-\d{2})\*\* — (\S+):.*?\[check: ([^\]]+)\]", re.MULTILINE
)

# A verifier maps a ref string -> (ok, detail).
Verifier = Callable[[str], "tuple[bool, str]"]


class PromoteOutcome:
    """Result of an auto-promote pass over the ledger text. Value object."""

    __slots__ = ("new_text", "promoted", "regressed")

    def __init__(
        self,
        new_text: str,
        promoted: list[tuple[str, str, str]],
        regressed: list[tuple[str, str, str]],
    ) -> None:
        self.new_text = new_text
        # each tuple: (signal, ref, detail)
        self.promoted = promoted
        self.regressed = regressed

    @property
    def changed(self) -> bool:
        return bool(self.promoted)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"PromoteOutcome(promoted={len(self.promoted)}, regressed={len(self.regressed)})"


def _sourced_refs(text: str) -> dict[str, tuple[str, str]]:
    """signal -> (ref, source_date) for the most recent verifiable source judgment."""
    out: dict[str, tuple[str, str]] = {}
    for m in _SOURCE_RE.finditer(text):
        source_date, signal, ref = m.group(1), m.group(2), m.group(3).strip()
        prev = out.get(signal)
        if prev is None or source_date >= prev[1]:
            out[signal] = (ref, source_date)
    return out


def auto_promote(text: str, *, verify: Verifier, today: datetime.date) -> PromoteOutcome:
    """Promote candidates whose signal has a verifiable source judgment.

    Pure: the ``verify`` callable decides whether a ref resolves, so the
    promotion logic is testable without a real repo tree. A candidate is promoted
    only when (a) its signal has a source judgment carrying a ``[check: ref]`` and
    (b) ``verify(ref)`` returns ok. A ref that fails to verify yields a
    *regressed* entry and the candidate is left untouched (a human must look).
    """
    sourced = _sourced_refs(text)
    promoted: list[tuple[str, str, str]] = []
    regressed: list[tuple[str, str, str]] = []
    seen_regressed: set[str] = set()
    today_iso = today.isoformat()

    def _replace(m: re.Match[str]) -> str:
        cand_date, signal, context = m.group(1), m.group(2), m.group(3).strip()
        ref_src = sourced.get(signal)
        if ref_src is None:
            return m.group(0)  # no source judgment — leave for the human/observe step
        ref, source_date = ref_src
        ok, detail = verify(ref)
        if not ok:
            if signal not in seen_regressed:
                seen_regressed.add(signal)
                regressed.append((signal, ref, detail))
            return m.group(0)  # encoded check rotted — refuse to promote
        promoted.append((signal, ref, source_date))
        # Reconfirmation, NOT a new source: uses [reconfirmed: …], which the
        # source regex ignores, so auto lines never breed further auto-promotions.
        return (
            f"- **{cand_date}** — {signal}: recurred ({context}) → **judgment:** "
            f"auto-reconfirmed against {source_date} "
            f"[reconfirmed: {ref} @ {today_iso}]"
        )

    new_text = _CANDIDATE_LINE_RE.sub(_replace, text)
    return PromoteOutcome(new_text, promoted, regressed)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def verify_ref(ref: str, *, repos_root: Path) -> tuple[bool, str]:
    """File-read verification of a check ref. No command execution.

    Returns (ok, detail). An unknown/malformed kind is (False, reason) so a
    typo'd ref surfaces as a regression rather than silently passing.
    """
    parts = ref.split(":")
    kind = parts[0] if parts else ""

    if kind == "path" and len(parts) == 3:
        _, repo, rel = parts
        return ((repos_root / repo / rel).exists(), f"path {repo}/{rel}")

    if kind == "custodian" and len(parts) == 3:
        _, repo, detector = parts
        text = _read(repos_root / repo / ".custodian.yaml")
        if text is None:
            return (False, f"{repo}/.custodian.yaml missing")
        return (detector in text, f"custodian {detector} in {repo}")

    if kind == "ci" and len(parts) == 4:
        _, repo, wf, job = parts
        text = _read(repos_root / repo / ".github" / "workflows" / wf)
        if text is None:
            return (False, f"{repo}/.github/workflows/{wf} missing")
        return (job in text, f"ci {job} in {repo}/{wf}")

    return (False, f"unrecognised check ref '{ref}'")


def promote(
    *,
    repos_root: Path,
    today: datetime.date | None = None,
    private_root: Path | None = None,
    write: bool = True,
) -> PromoteOutcome:
    """Read the ledger, auto-promote what verifies, and (optionally) write it back.

    Raises LedgerUnavailable if the private manifest cannot be resolved; returns
    an empty outcome when the ledger file does not exist yet.
    """
    path = ledger_path(private_root)  # may raise LedgerUnavailable
    if not path.exists():
        return PromoteOutcome("", [], [])
    text = path.read_text(encoding="utf-8")
    outcome = auto_promote(
        text,
        verify=lambda r: verify_ref(r, repos_root=repos_root),
        today=today or datetime.date.today(),
    )
    if write and outcome.changed:
        path.write_text(outcome.new_text, encoding="utf-8")
    return outcome


__all__ = [
    "LedgerUnavailable",
    "PromoteOutcome",
    "Verifier",
    "auto_promote",
    "promote",
    "verify_ref",
]
