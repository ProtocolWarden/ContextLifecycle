"""InvestigationCapsule — mirrors .context/schemas/investigation_capsule.yaml."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CapsuleExclusions(BaseModel):
    model_config = ConfigDict(extra="allow")
    reload_forbidden: list[str] = Field(default_factory=list)
    retry_forbidden: list[str] = Field(default_factory=list)


class InvestigationCapsule(BaseModel):
    """The core resumable cognition primitive. Field names match the YAML schema verbatim."""

    model_config = ConfigDict(extra="allow")

    # Identity envelope
    capsule_id: str = ""
    schema_version: str = "0.1"
    created_at: str = ""
    updated_at: str = ""
    created_by: str = ""
    parent_capsule_id: str = ""
    related_checkpoint_id: str = ""
    status: str = ""

    # Investigation state
    current_blocker: str = ""
    current_phase: str = ""
    failing_invariant: str = ""
    active_hypotheses: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    recent_failures: list[dict[str, Any]] = Field(default_factory=list)
    attempted_remediations: list[dict[str, Any]] = Field(default_factory=list)
    next_actions: list[str] = Field(default_factory=list)

    # Safety constraints
    safety_constraints: list[str] = Field(default_factory=list)
    known_safe_boundaries: list[str] = Field(default_factory=list)

    # Exclusions
    exclusions: CapsuleExclusions = Field(default_factory=CapsuleExclusions)

    # Handoff
    handoff_notes: str = ""

    def is_well_formed(self) -> tuple[bool, str]:
        """Mirror the bash hook's required-field validation."""
        required = ["capsule_id", "schema_version", "status"]
        missing = [k for k in required if not getattr(self, k, None)]
        if missing:
            return False, "missing:" + ",".join(missing)
        return True, "ok"
