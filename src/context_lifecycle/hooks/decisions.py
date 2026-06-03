# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Allow/Block/Warn result types for hook decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Decision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    # Warns are non-blocking notes attached to an Allow decision.


@dataclass(frozen=True)
class Warn:
    reason: str


@dataclass
class DecisionResult:
    """Result of evaluating a hook's checks.

    `decision` is the final allow/block verdict. Warns are accumulated
    regardless and emitted to stderr by the CLI layer.
    """

    decision: Decision = Decision.ALLOW
    reason: str = ""
    warnings: list[Warn] = field(default_factory=list)

    def block(self, reason: str) -> "DecisionResult":
        # First block wins (matches bash's `exit 2` short-circuit).
        if self.decision is Decision.ALLOW:
            self.decision = Decision.BLOCK
            self.reason = reason
        return self

    def warn(self, reason: str) -> "DecisionResult":
        self.warnings.append(Warn(reason))
        return self

    @property
    def is_block(self) -> bool:
        return self.decision is Decision.BLOCK


def Allow() -> DecisionResult:
    return DecisionResult()


def Block(reason: str) -> DecisionResult:
    return DecisionResult(decision=Decision.BLOCK, reason=reason)
