"""Stop hook decision logic. Port of adapters/claude/hooks/stop.sh.

Enforces a LoopCheckpoint was written during this session. Resolution:
- If the session marker exists, only checkpoints newer than the marker count.
- Otherwise (session with no tool calls), any checkpoint counts.

The bash hook is structurally non-fatal on Stop (Claude Code can't always
hard-block session end); we mirror that by emitting prominent stderr and
returning ALLOW with warnings. The CLI never exits 2 from stop.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from context_lifecycle.hooks.decisions import Allow, DecisionResult
from context_lifecycle.io.yaml_io import load_yaml_safe
from context_lifecycle.models.config import CLConfig
from context_lifecycle.session.paths import SessionPaths


@dataclass
class StopReport:
    decision: DecisionResult
    enforcement_message: str | None = None  # prominent stderr line


def _has_fresh_checkpoint(checkpoints_dir: Path, marker: Path | None) -> bool:
    if not checkpoints_dir.is_dir():
        return False
    yamls = [p for p in checkpoints_dir.iterdir() if p.suffix == ".yaml" and p.name != ".gitkeep"]
    if not yamls:
        return False
    if marker is None or not marker.exists():
        return True
    marker_mtime = marker.stat().st_mtime
    return any(p.stat().st_mtime > marker_mtime for p in yamls)


def evaluate_stop(
    *,
    paths: SessionPaths,
    config: CLConfig,
    session_marker: Path | None = None,
) -> StopReport:
    result = Allow()
    enforcement_message: str | None = None

    has_checkpoint = _has_fresh_checkpoint(paths.checkpoints, session_marker)
    if not has_checkpoint:
        if config.loop.checkpoint_on_stop:
            enforcement_message = (
                "ContextGuard: Session ending without a LoopCheckpoint. "
                "Write a checkpoint before terminating.\n"
                f"  Create: {paths.checkpoints}/<checkpoint-id>.yaml\n"
                f"  Template: {paths.context_root}/templates/loop_checkpoint.template.yaml"
            )
        else:
            result.warn("Session ending without a LoopCheckpoint.")

    # Active-capsule status check
    if paths.active.is_dir():
        active_yamls = sorted(
            p for p in paths.active.iterdir() if p.suffix == ".yaml" and p.name != ".gitkeep"
        )
        if active_yamls:
            cap = active_yamls[0]
            data = load_yaml_safe(cap)
            status = (data or {}).get("status", "active") if isinstance(data, dict) else "active"
            if status == "active":
                result.warn(
                    f"Active capsule '{cap.name}' status is still 'active'. "
                    "Update status or handoff_notes before terminating."
                )

    return StopReport(decision=result, enforcement_message=enforcement_message)
