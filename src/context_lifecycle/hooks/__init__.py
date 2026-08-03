# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Hook decision functions — pure logic over loaded state."""

from context_lifecycle.hooks.decisions import (
    Allow,
    Block,
    Decision,
    DecisionResult,
    Warn,
)
from context_lifecycle.hooks.pre_tool_use import evaluate_pre_tool_use
from context_lifecycle.hooks.stop import evaluate_stop

__all__ = [
    "Allow",
    "Block",
    "Decision",
    "DecisionResult",
    "Warn",
    "evaluate_pre_tool_use",
    "evaluate_stop",
]
