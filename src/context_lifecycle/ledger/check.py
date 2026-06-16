# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Staleness check for the operator-interventions ledger.

Closes the capture→promote loop: a candidate that sits unpromoted for too long
is a signal the operator hasn't triaged it. ``cl ledger check`` surfaces those so
they get a judgment line or get dropped — capture without promotion is just an
accreting pile, which is the failure mode the ledger is built to avoid.

A *candidate* is an unpromoted line (``- [ ] CANDIDATE <date> ...``). Promotion
removes the CANDIDATE marker, so promoted entries are never flagged.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from context_lifecycle.ledger.capture import LedgerUnavailable, ledger_path

DEFAULT_MAX_AGE_DAYS = 14

# - [ ] CANDIDATE 2026-06-16 <signal> — <context> — judgment: ___
_CANDIDATE_RE = re.compile(
    r"^- \[ \] CANDIDATE (\d{4}-\d{2}-\d{2}) (\S+) — (.*?) — judgment:", re.MULTILINE
)


class StaleCandidate:
    """A candidate older than the threshold. Plain value object."""

    __slots__ = ("date", "signal", "context", "age_days")

    def __init__(self, date: str, signal: str, context: str, age_days: int) -> None:
        self.date = date
        self.signal = signal
        self.context = context
        self.age_days = age_days

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"StaleCandidate({self.date} {self.signal} +{self.age_days}d)"


def find_stale_candidates(
    text: str, *, max_age_days: int, today: datetime.date
) -> list[StaleCandidate]:
    """Return candidates whose age exceeds ``max_age_days``, oldest first.

    Unparseable dates are skipped (never crash the check on a hand-edited line).
    """
    stale: list[StaleCandidate] = []
    for m in _CANDIDATE_RE.finditer(text):
        date_str, signal, context = m.group(1), m.group(2), m.group(3).strip()
        try:
            entry_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        age = (today - entry_date).days
        if age > max_age_days:
            stale.append(StaleCandidate(date_str, signal, context, age))
    stale.sort(key=lambda c: c.age_days, reverse=True)
    return stale


def check(
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    today: datetime.date | None = None,
    private_root: Path | None = None,
) -> list[StaleCandidate]:
    """Resolve the ledger and return its stale candidates.

    Raises LedgerUnavailable if no private manifest resolves; returns [] when the
    ledger file simply doesn't exist yet (nothing to check).
    """
    path = ledger_path(private_root)  # may raise LedgerUnavailable
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return find_stale_candidates(
        text, max_age_days=max_age_days, today=today or datetime.date.today()
    )


__all__ = ["DEFAULT_MAX_AGE_DAYS", "LedgerUnavailable", "StaleCandidate", "check", "find_stale_candidates"]
