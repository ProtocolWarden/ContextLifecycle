"""WorkerHandoff — mirrors .context/schemas/worker_handoff.yaml."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class WorkerScope(BaseModel):
    model_config = ConfigDict(extra="allow")
    repo: str = ""
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    allowed_subagents: int = 0
    mutation_policy: str = ""


class Lease(BaseModel):
    model_config = ConfigDict(extra="allow")
    max_minutes: int = 0
    max_tool_calls: int = 0
    max_subagents: int = -1  # -1 = unset; 0 = explicitly forbidden
    expires_at: str = ""
    escalation_required_after: str = ""


class WorkerHandoff(BaseModel):
    model_config = ConfigDict(extra="allow")

    handoff_id: str = ""
    schema_version: str = "0.1"
    created_at: str = ""
    source_checkpoint_id: str = ""
    source_capsule_id: str = ""
    target_worker_id: str = ""

    task_description: str = ""
    input_capsule_id: str = ""
    input_artifacts: list[str] = Field(default_factory=list)
    expected_output: str = ""
    completion_criteria: str = ""
    validation_command: str = ""
    escalation_condition: str = ""

    worker_scope: WorkerScope = Field(default_factory=WorkerScope)
    lease: Lease = Field(default_factory=Lease)

    # Compatibility shim: schemas put `expires_at` under lease, but the bash
    # hook also read a top-level `expires_at`. Allow either via extra="allow".
    @property
    def effective_expires_at(self) -> str:
        if self.lease.expires_at:
            return self.lease.expires_at
        return getattr(self, "expires_at", "") or ""

    def is_lease_expired(self, now: datetime | None = None) -> bool:
        ts = self.effective_expires_at
        if not ts:
            return False
        now = now or datetime.now(timezone.utc)
        try:
            # Accept "Z" suffix and offset-aware ISO formats
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return now > parsed
