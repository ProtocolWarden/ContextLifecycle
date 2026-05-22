"""GuardConfig — mirrors the .context/config.yaml structure consumed by hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GuardConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    require_capsule: bool = False
    enforce_lease: bool = True
    capsule_path: str = ".context/active/"
    checkpoint_path: str = ".context/checkpoints/"
    handoff_path: str = ".context/handoffs/"


class LoopConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    checkpoint_on_stop: bool = True


class CLConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    clp_version: str = "0.1"
    repo: str = ""
    guard: GuardConfig = Field(default_factory=GuardConfig)
    loop: LoopConfig = Field(default_factory=LoopConfig)

    @classmethod
    def from_file(cls, path: Path) -> "CLConfig":
        """Load from YAML file. Returns defaults if file missing or malformed."""
        from context_lifecycle.io.yaml_io import load_yaml_safe

        data: Any = load_yaml_safe(path, default=None)
        if not isinstance(data, dict):
            return cls()
        return cls.model_validate(data)
