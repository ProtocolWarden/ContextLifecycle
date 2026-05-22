"""Hook decision functions — pure logic over loaded state."""

from context_lifecycle.hooks.decisions import (
    Decision,
    Allow,
    Block,
    Warn,
    DecisionResult,
)
from context_lifecycle.hooks.pre_tool_use import evaluate_pre_tool_use
from context_lifecycle.hooks.stop import evaluate_stop

__all__ = [
    "Decision",
    "Allow",
    "Block",
    "Warn",
    "DecisionResult",
    "evaluate_pre_tool_use",
    "evaluate_stop",
]
