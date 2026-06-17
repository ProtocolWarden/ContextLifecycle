# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Operator-interventions ledger — capture half only.

The ledger records moments a *human* had to step in (a worker PR a human
closed/overrode, a manual op a worker normally does). It is the dataset for
deciding which judgment to encode next as a verifiable check, then step back.

The loop has four steps:

- **capture** — append an *unjudged candidate* (a fleet signal fired). Never judges.
- **observe** — cluster candidates by recurring signal; surface novel patterns
  awaiting a *first* human judgment. Counts, never judges.
- **promote** — the one machine-allowed promotion: a *re-verification*. A signal
  earns its judgment once from a human, who appends a machine-readable
  ``[check: <ref>]``; thereafter each recurrence is auto-promoted by confirming
  that check is still live. Invents no judgment; flags rotted checks as regressed.
- The residual — free-text judgments with nothing to verify against — stays
  manual. Auto-writing those would manufacture the false confidence the whole
  discipline exists to prevent.
"""

from __future__ import annotations

from context_lifecycle.ledger.capture import (
    LEDGER_SUBPATH,
    LedgerUnavailable,
    capture,
    ledger_path,
)
from context_lifecycle.ledger.observe import RecurringSignal, find_recurring, observe
from context_lifecycle.ledger.promote import (
    PromoteOutcome,
    auto_promote,
    promote,
    verify_ref,
)

__all__ = [
    "LEDGER_SUBPATH",
    "LedgerUnavailable",
    "PromoteOutcome",
    "RecurringSignal",
    "auto_promote",
    "capture",
    "find_recurring",
    "ledger_path",
    "observe",
    "promote",
    "verify_ref",
]
