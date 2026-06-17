# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Append an unjudged candidate to the operator-interventions ledger.

The ledger file lives in the private-manifest repo (operator state, git-tracked,
archive-backed) at ``<private-root>/ledger/operator-interventions.md``. A
candidate line has a distinct, greppable shape so promoted (judged) entries are
trivially told apart and a later staleness check can flag candidates left
unpromoted:

    - [ ] CANDIDATE <date> <signal> — <context> — judgment: ___

Promotion is a human editing that line: checking the box, filling the judgment,
or deleting it. Capture never writes the judgment and never promotes.
"""

from __future__ import annotations

import datetime
from pathlib import Path

from context_lifecycle.reconcile.privacy import (
    PrivateArchiveUnavailable,
    resolve_private_root,
)

LEDGER_SUBPATH = Path("ledger") / "operator-interventions.md"

_CANDIDATE_MARKER = "- [ ] CANDIDATE"

_HEADER = """# Operator interventions ledger

Append-only log of moments a *human* had to step in or correct the fleet — the
dataset for deciding which judgment to encode next as a verifiable check, then
step back. You can't encode judgment you haven't observed.

Two entry states:

- **candidate** — auto-captured, *unjudged*:
  `- [ ] CANDIDATE <date> <signal> — <context> — judgment: ___`
- **promoted** — the judgment was filled and keep-or-drop decided. Rewrite the
  line into a `- **<date>** — <signal>: <what happened> → **judgment:**
  <encodable check>` bullet, or delete it if it isn't worth encoding.

A signal's *first* judgment is human (or signed-off). If it's an encodable
check, append a machine-readable ref so later recurrences self-verify:
`… → **judgment:** <text> [check: <kind>:<args>]`, where `<kind>` is one of
`custodian:<repo>:<detector>`, `ci:<repo>:<workflow>:<job>`, or
`path:<repo>:<relpath>`. `cl ledger promote` then auto-reconfirms each recurrence
of that signal and flags the check if it ever stops resolving (a regression).

Capture only ever adds candidates. Free-text judgments (nothing to verify) stay
manual — never auto-judge them.

## Entries
"""


class LedgerUnavailable(RuntimeError):
    """Raised when the ledger destination (private manifest) cannot be resolved."""


def ledger_path(private_root: Path | None = None) -> Path:
    """Return ``<private-root>/ledger/operator-interventions.md`` (not created here)."""
    try:
        root = private_root if private_root is not None else resolve_private_root()
    except PrivateArchiveUnavailable as exc:
        raise LedgerUnavailable(str(exc)) from exc
    return root / LEDGER_SUBPATH


def _today() -> str:
    return datetime.date.today().isoformat()


def _already_present(text: str, signal: str, context: str) -> bool:
    """True when an unpromoted candidate with this signal+context already exists.

    Dedup ignores the date so a signal that fires on every poll (a worker
    re-observing the same closed PR) does not pile up duplicate candidates.
    """
    needle = f"{signal} — {context} —"
    for line in text.splitlines():
        if line.startswith(_CANDIDATE_MARKER) and needle in line:
            return True
    return False


def capture(
    signal: str,
    context: str,
    *,
    date: str | None = None,
    private_root: Path | None = None,
) -> tuple[Path, bool]:
    """Append one candidate to the ledger. Returns (path, written).

    ``written`` is False when an identical unpromoted candidate already exists
    (dedup) — the call is then a no-op. Raises LedgerUnavailable if the private
    manifest cannot be resolved, so callers can fail soft.
    """
    signal = signal.strip()
    context = context.strip()
    if not signal:
        raise ValueError("signal must be non-empty")

    path = ledger_path(private_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if not existing:
        existing = _HEADER

    if _already_present(existing, signal, context):
        # Make sure the header at least exists, but add no duplicate row.
        if not path.exists():
            path.write_text(existing, encoding="utf-8")
        return path, False

    line = f"{_CANDIDATE_MARKER} {date or _today()} {signal} — {context} — judgment: ___\n"
    body = existing if existing.endswith("\n") else existing + "\n"
    path.write_text(body + line, encoding="utf-8")
    return path, True
