"""YAML load/dump helpers — no Pydantic dependency at this layer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_yaml(path: Path) -> Any:
    """Load a YAML file. Raises on parse error."""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_yaml_safe(path: Path, default: Any = None) -> Any:
    """Load YAML, returning `default` on any failure (file missing, parse error)."""
    try:
        return load_yaml(path)
    except (OSError, yaml.YAMLError):
        return default


def dump_yaml(path: Path, data: Any) -> None:
    """Dump data as YAML to a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
