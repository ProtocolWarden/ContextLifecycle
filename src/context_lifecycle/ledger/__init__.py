# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Operator-interventions ledger — capture half only.

The ledger records moments a *human* had to step in (a worker PR a human
closed/overrode, a manual op a worker normally does). It is the dataset for
deciding which judgment to encode next as a verifiable check, then step back.

This package owns **capture** only: appending an *unjudged candidate* to the
ledger living in the private-manifest repo. The **interpretation** half — adding
the ``judgment:`` line and deciding keep-or-drop ("promotion") — stays manual by
design. Auto-writing judgment would manufacture the false confidence the whole
discipline exists to prevent; a firehose of unjudged auto-entries destroys the
signal that makes the hand-judged entries valuable. So: auto-capture candidates,
manual promote, never auto-promote.
"""

from __future__ import annotations

from context_lifecycle.ledger.capture import (
    LEDGER_SUBPATH,
    LedgerUnavailable,
    capture,
    ledger_path,
)

__all__ = ["LEDGER_SUBPATH", "LedgerUnavailable", "capture", "ledger_path"]
