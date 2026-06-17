# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Observe step — cluster ledger candidates by recurring signal.

Capture produces a flat list of unjudged candidates. The *observe* step is the
controller reading that list and noticing a signal that keeps recurring — the
cue that a pattern is worth encoding as a check (a first human judgment), after
which the [[promote]] step can reconfirm-and-clear every later recurrence.

This step makes **no judgment**: it only counts. A cluster is a signal that
appears on at least ``min_count`` candidate lines within ``window_days``. It
deliberately ignores signals that already have a verifiable promoted judgment —
those are the promoter's job, not a human's; observe surfaces the *novel*
patterns awaiting a first call.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from context_lifecycle.ledger.capture import LedgerUnavailable, ledger_path

DEFAULT_MIN_COUNT = 3
DEFAULT_WINDOW_DAYS = 30

# - [ ] CANDIDATE 2026-06-16 <signal> — <context> — judgment: ___
_CANDIDATE_RE = re.compile(
    r"^- \[ \] CANDIDATE (\d{4}-\d{2}-\d{2}) (\S+) — (.*?) — judgment:", re.MULTILINE
)
# A promoted judgment that already carries a machine-checkable ref — the signals
# the promoter owns, which observe must NOT re-surface as "needs a human".
#   - **2026-05-01** — green-gate-override: ... → **judgment:** ... [check: ci:...]
_SOURCED_RE = re.compile(r"^- \*\*\d{4}-\d{2}-\d{2}\*\* — (\S+):.*?\[check: ", re.MULTILINE)


class RecurringSignal:
    """A signal recurring across candidates within the window. Value object."""

    __slots__ = ("signal", "count", "latest_date", "contexts")

    def __init__(
        self, signal: str, count: int, latest_date: str, contexts: list[str]
    ) -> None:
        self.signal = signal
        self.count = count
        self.latest_date = latest_date
        self.contexts = contexts

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"RecurringSignal({self.signal} x{self.count})"


def find_recurring(
    text: str,
    *,
    min_count: int = DEFAULT_MIN_COUNT,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: datetime.date,
) -> list[RecurringSignal]:
    """Return signals recurring >= ``min_count`` within ``window_days``, hottest first.

    A signal that already carries a verifiable promoted judgment (``[check: …]``)
    is skipped — it has been judged once, so its recurrences belong to the
    promoter, not to a human triager. Unparseable dates are ignored.
    """
    sourced = {m.group(1) for m in _SOURCED_RE.finditer(text)}
    by_signal: dict[str, list[tuple[str, str]]] = {}
    for m in _CANDIDATE_RE.finditer(text):
        date_str, signal, context = m.group(1), m.group(2), m.group(3).strip()
        if signal in sourced:
            continue
        try:
            entry_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            continue
        if (today - entry_date).days > window_days:
            continue
        by_signal.setdefault(signal, []).append((date_str, context))

    clusters: list[RecurringSignal] = []
    for signal, rows in by_signal.items():
        if len(rows) < min_count:
            continue
        latest = max(d for d, _ in rows)
        clusters.append(RecurringSignal(signal, len(rows), latest, [c for _, c in rows]))
    clusters.sort(key=lambda c: (c.count, c.latest_date), reverse=True)
    return clusters


def observe(
    *,
    min_count: int = DEFAULT_MIN_COUNT,
    window_days: int = DEFAULT_WINDOW_DAYS,
    today: datetime.date | None = None,
    private_root: Path | None = None,
) -> list[RecurringSignal]:
    """Resolve the ledger and return its recurring (unjudged) signals.

    Raises LedgerUnavailable if no private manifest resolves; returns [] when the
    ledger file does not exist yet (nothing to observe).
    """
    path = ledger_path(private_root)  # may raise LedgerUnavailable
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return find_recurring(
        text,
        min_count=min_count,
        window_days=window_days,
        today=today or datetime.date.today(),
    )


__all__ = [
    "DEFAULT_MIN_COUNT",
    "DEFAULT_WINDOW_DAYS",
    "LedgerUnavailable",
    "RecurringSignal",
    "find_recurring",
    "observe",
]
