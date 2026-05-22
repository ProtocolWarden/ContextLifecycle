"""Pydantic model round-trip and validation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from context_lifecycle.models.capsule import InvestigationCapsule
from context_lifecycle.models.checkpoint import LoopCheckpoint
from context_lifecycle.models.config import CLConfig
from context_lifecycle.models.handoff import WorkerHandoff


def test_capsule_defaults():
    c = InvestigationCapsule()
    ok, msg = c.is_well_formed()
    assert not ok
    assert "missing" in msg


def test_capsule_well_formed():
    c = InvestigationCapsule(capsule_id="x", schema_version="0.1", status="active")
    ok, msg = c.is_well_formed()
    assert ok and msg == "ok"


def test_capsule_extra_fields_allowed():
    c = InvestigationCapsule.model_validate(
        {"capsule_id": "x", "schema_version": "0.1", "status": "active", "custom_field": 42}
    )
    assert c.capsule_id == "x"


def test_checkpoint_context_risk_defaults():
    cp = LoopCheckpoint()
    assert cp.orchestrator.context_risk.high_parallelism is False
    assert cp.orchestrator.context_risk.checkpoint_stale is False


def test_checkpoint_loads_risk_flags():
    cp = LoopCheckpoint.model_validate(
        {"orchestrator": {"context_risk": {"checkpoint_stale": True, "high_parallelism": True}}}
    )
    assert cp.orchestrator.context_risk.checkpoint_stale is True
    assert cp.orchestrator.context_risk.high_parallelism is True


def test_handoff_lease_not_expired():
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = WorkerHandoff.model_validate({"lease": {"expires_at": future}})
    assert h.is_lease_expired() is False


def test_handoff_lease_expired():
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = WorkerHandoff.model_validate({"lease": {"expires_at": past}})
    assert h.is_lease_expired() is True


def test_handoff_lease_unset_means_not_expired():
    h = WorkerHandoff()
    assert h.is_lease_expired() is False


def test_handoff_top_level_expires_at_compat():
    """The bash hook read top-level `expires_at`; allow it via extra='allow'."""
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    h = WorkerHandoff.model_validate({"expires_at": past})
    assert h.is_lease_expired() is True


def test_config_defaults():
    c = CLConfig()
    assert c.guard.require_capsule is False
    assert c.guard.enforce_lease is True
    assert c.loop.checkpoint_on_stop is True


def test_config_overrides():
    c = CLConfig.model_validate(
        {"guard": {"require_capsule": True}, "loop": {"checkpoint_on_stop": False}}
    )
    assert c.guard.require_capsule is True
    assert c.loop.checkpoint_on_stop is False
