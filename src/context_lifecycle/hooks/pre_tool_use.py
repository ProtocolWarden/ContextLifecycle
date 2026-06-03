# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""PreToolUse hook decision logic. Port of adapters/claude/hooks/pre_tool_use.sh.

Reads cognition state from the *anchoring manifest's* `.context/sessions/<sid>/`,
not from the consumer repo. Decision tree mirrors the bash reference verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from context_lifecycle.hooks.decisions import Allow, DecisionResult
from context_lifecycle.io.yaml_io import load_yaml_safe
from context_lifecycle.models.capsule import InvestigationCapsule
from context_lifecycle.models.checkpoint import LoopCheckpoint
from context_lifecycle.models.config import CLConfig
from context_lifecycle.models.handoff import WorkerHandoff
from context_lifecycle.session.paths import SessionPaths


@dataclass
class HookInput:
    tool_name: str
    tool_input: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "HookInput":
        return cls(
            tool_name=str(payload.get("tool_name", "")),
            tool_input=payload.get("tool_input") or {},
        )

    @property
    def target_path(self) -> str:
        ti = self.tool_input
        return str(ti.get("file_path") or ti.get("path") or "")


def _first_yaml(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    candidates = sorted(p for p in directory.iterdir() if p.suffix == ".yaml" and p.name != ".gitkeep")
    return candidates[0] if candidates else None


def _latest_yaml(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    candidates = sorted(p for p in directory.iterdir() if p.suffix == ".yaml" and p.name != ".gitkeep")
    return candidates[-1] if candidates else None


def _load_model(path: Path | None, model):
    """Validate the YAML at `path` into `model`, or return None on any failure."""
    if path is None:
        return None
    data = load_yaml_safe(path)
    if not isinstance(data, dict):
        return None
    try:
        return model.model_validate(data)
    except Exception:
        return None


def _load_handoff(directory: Path) -> WorkerHandoff | None:
    return _load_model(_first_yaml(directory), WorkerHandoff)


def _load_checkpoint(directory: Path) -> LoopCheckpoint | None:
    return _load_model(_latest_yaml(directory), LoopCheckpoint)


def _path_has_prefix(target: str, prefix: str) -> bool:
    """Mirror the bash `[[ "$TARGET_PATH" == "$forbidden_path"* ]]` glob."""
    return bool(prefix) and target.startswith(prefix)


def evaluate_pre_tool_use(
    *,
    paths: SessionPaths,
    config: CLConfig,
    hook_input: HookInput,
    now: datetime | None = None,
) -> DecisionResult:
    """Pure decision function. Returns a DecisionResult; CLI maps it to exit codes."""
    result = Allow()
    now = now or datetime.now(timezone.utc)

    # --- require_capsule ---
    if config.guard.require_capsule:
        capsule_path = _first_yaml(paths.active)
        if capsule_path is None:
            return result.block(
                f"ContextGuard: No active capsule found in {paths.active}. "
                "Create or load an InvestigationCapsule before proceeding."
            )
        data = load_yaml_safe(capsule_path)
        if not isinstance(data, dict):
            return result.block(
                f"ContextGuard: Active capsule is invalid (malformed). "
                f"Fix or remove {capsule_path} before proceeding."
            )
        try:
            capsule = InvestigationCapsule.model_validate(data)
        except Exception as e:  # pragma: no cover - structural
            return result.block(
                f"ContextGuard: Active capsule is invalid ({e}). "
                f"Fix or remove {capsule_path} before proceeding."
            )
        ok, msg = capsule.is_well_formed()
        if not ok:
            return result.block(
                f"ContextGuard: Active capsule is invalid ({msg}). "
                f"Fix or remove {capsule_path} before proceeding."
            )

    # --- lease expiry ---
    handoff = _load_handoff(paths.handoffs)
    if config.guard.enforce_lease and handoff is not None:
        if handoff.is_lease_expired(now=now):
            return result.block(
                f"ContextGuard: Lease expired at {handoff.effective_expires_at}. "
                "Write a LoopCheckpoint and escalate before continuing."
            )

    # --- pre_write checks (Write/Edit) ---
    if hook_input.tool_name in ("Write", "Edit") and handoff is not None:
        target = hook_input.target_path
        if target:
            scope = handoff.worker_scope
            # forbidden_paths
            for forbidden in scope.forbidden_paths or []:
                if _path_has_prefix(target, forbidden):
                    return result.block(
                        f"ContextGuard: Path '{target}' is forbidden by active worker scope "
                        f"(matches '{forbidden}')."
                    )
            # allowed_paths whitelist
            allowed = scope.allowed_paths or []
            if allowed:
                if not any(_path_has_prefix(target, a) for a in allowed):
                    return result.block(
                        f"ContextGuard: Path '{target}' is outside worker scope allowed_paths. "
                        f"Permitted prefixes: {' '.join(allowed)}"
                    )
            # mutation_policy
            if scope.mutation_policy == "read_only":
                return result.block(
                    "ContextGuard: Worker scope is read_only. Write operations are not permitted."
                )

    # --- pre_spawn checks (Agent) ---
    checkpoint = _load_checkpoint(paths.checkpoints)
    if hook_input.tool_name == "Agent":
        if handoff is not None and handoff.lease.max_subagents == 0:
            return result.block(
                "ContextGuard: Active lease prohibits subagent spawning (max_subagents: 0)."
            )
        if checkpoint is not None:
            risk = checkpoint.orchestrator.context_risk
            if risk.high_parallelism:
                return result.block(
                    "ContextGuard: context_risk.high_parallelism is true. "
                    "Deny additional worker spawning until resolved."
                )
            if risk.subagent_heavy:
                result.warn(
                    "ContextGuard: context_risk.subagent_heavy is true. "
                    "Reduce subagent budget and avoid Explore escalation."
                )

    # --- context_risk flags from latest checkpoint (all tools) ---
    if checkpoint is not None:
        risk = checkpoint.orchestrator.context_risk
        if risk.long_lived_session:
            result.warn(
                "ContextGuard: context_risk.long_lived_session is true. "
                "Compact context before continuing."
            )
        if risk.checkpoint_stale:
            return result.block(
                "ContextGuard: context_risk.checkpoint_stale is true. "
                "Write a fresh LoopCheckpoint before dispatching."
            )
        if risk.reload_scope_too_large and hook_input.tool_name in ("Read", "Bash", "Glob"):
            result.warn(
                "ContextGuard: context_risk.reload_scope_too_large is true. "
                "Prune warm/cold context before broad reads."
            )

    return result
