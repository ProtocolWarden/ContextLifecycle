# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Shared pytest fixtures for ContextLifecycle tests.

Includes a venv guard: refuses to run unless invoked from inside this project's
`.venv` — prevents accidental test runs against the global interpreter.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

_REPO = Path(__file__).resolve().parent.parent
_EXPECTED_VENV = _REPO / ".venv"


def _running_in_venv() -> bool:
    return Path(sys.prefix).resolve() == _EXPECTED_VENV.resolve()


if not _running_in_venv() and not os.environ.get("CUSTODIAN_SKIP_VENV_GUARD"):
    sys.stderr.write(
        f"ERROR: Tests must be run inside this project's virtual environment.\n"
        f"Expected: {_EXPECTED_VENV}\n"
        f"Active:   {sys.prefix}\n\n"
        f"Activate it first:\n"
        f"  source .venv/bin/activate\n"
        f"Or invoke pytest through the venv directly:\n"
        f"  .venv/bin/pytest\n"
    )
    sys.exit(2)

from context_lifecycle.models.config import CLConfig  # noqa: E402  (after venv guard)
from context_lifecycle.session.paths import SessionPaths  # noqa: E402


@pytest.fixture
def anchor(tmp_path: Path) -> Path:
    """A bare anchor manifest directory with .context/ skeleton."""
    (tmp_path / ".context").mkdir(parents=True)
    return tmp_path


@pytest.fixture
def session_id() -> str:
    return "s-2026-05-22-a1b2"


@pytest.fixture
def paths(anchor: Path, session_id: str) -> SessionPaths:
    p = SessionPaths(anchor=anchor, session_id=session_id)
    p.ensure()
    return p


@pytest.fixture
def default_config() -> CLConfig:
    return CLConfig()


@pytest.fixture
def require_capsule_config() -> CLConfig:
    return CLConfig.model_validate(
        {"guard": {"require_capsule": True, "enforce_lease": True}}
    )


def write_yaml(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)
    return path


@pytest.fixture
def write_yaml_helper():
    return write_yaml


@pytest.fixture
def valid_capsule_data() -> dict:
    return {
        "capsule_id": "cap-test-001",
        "schema_version": "0.1",
        "status": "active",
        "current_blocker": "none",
    }


@pytest.fixture
def valid_handoff_data() -> dict:
    return {
        "handoff_id": "h-001",
        "schema_version": "0.1",
        "worker_scope": {
            "repo": "TestRepo",
            "allowed_paths": ["src/", "tests/"],
            "forbidden_paths": [".git/", "secrets/"],
            "mutation_policy": "write_allowed",
        },
        "lease": {
            "max_subagents": 2,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1))
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }


@pytest.fixture
def valid_checkpoint_data() -> dict:
    return {
        "checkpoint_id": "ckpt-001",
        "schema_version": "0.1",
        "orchestrator": {
            "context_risk": {
                "long_lived_session": False,
                "high_parallelism": False,
                "subagent_heavy": False,
                "checkpoint_stale": False,
                "reload_scope_too_large": False,
            }
        },
    }


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """Strip CL_ANCHOR/CL_SESSION_ID so tests start clean."""
    monkeypatch.delenv("CL_ANCHOR", raising=False)
    monkeypatch.delenv("CL_SESSION_ID", raising=False)
    yield
