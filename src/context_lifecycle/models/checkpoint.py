"""LoopCheckpoint — mirrors .context/schemas/loop_checkpoint.yaml."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContextRisk(BaseModel):
    model_config = ConfigDict(extra="allow")
    long_lived_session: bool = False
    high_parallelism: bool = False
    subagent_heavy: bool = False
    checkpoint_stale: bool = False
    reload_scope_too_large: bool = False


class Orchestrator(BaseModel):
    model_config = ConfigDict(extra="allow")
    last_compacted_at: str = ""
    current_cycle_id: str = ""
    active_worker_ids: list[str] = Field(default_factory=list)
    active_capsule_ids: list[str] = Field(default_factory=list)
    next_allowed_action: str = ""
    context_risk: ContextRisk = Field(default_factory=ContextRisk)


class RelaunchMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")
    relaunch_command: str = ""
    relaunch_args: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)


class LoopCheckpoint(BaseModel):
    model_config = ConfigDict(extra="allow")

    checkpoint_id: str = ""
    schema_version: str = "0.1"
    created_at: str = ""
    updated_at: str = ""
    parent_checkpoint_id: str = ""
    active_capsule_ids: list[str] = Field(default_factory=list)
    orchestrator_cycle_id: str = ""

    last_completed_cycle: str = ""
    current_phase: str = ""
    current_operational_state: str = ""

    latest_worker_outputs: list[dict[str, Any]] = Field(default_factory=list)
    unresolved_blockers: list[dict[str, Any]] = Field(default_factory=list)

    next_scheduled_action: str = ""
    next_wakeup_reason: str = ""

    relaunch_metadata: RelaunchMetadata = Field(default_factory=RelaunchMetadata)
    compaction_status: str = ""

    orchestrator: Orchestrator = Field(default_factory=Orchestrator)

    operator_summary: str = ""
