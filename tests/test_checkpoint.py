"""LoopCheckpoint model validation tests."""

from __future__ import annotations

from context_lifecycle.models.checkpoint import LoopCheckpoint


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


def test_context_risk_all_flags_default_false():
    from context_lifecycle.models.checkpoint import ContextRisk

    r = ContextRisk()
    assert not any(
        [r.long_lived_session, r.high_parallelism, r.subagent_heavy,
         r.checkpoint_stale, r.reload_scope_too_large]
    )


def test_orchestrator_holds_context_risk():
    from context_lifecycle.models.checkpoint import ContextRisk, Orchestrator

    o = Orchestrator.model_validate({"current_cycle_id": "c1", "active_worker_ids": ["w1"]})
    assert o.current_cycle_id == "c1"
    assert o.active_worker_ids == ["w1"]
    assert isinstance(o.context_risk, ContextRisk)


def test_relaunch_metadata_fields():
    from context_lifecycle.models.checkpoint import RelaunchMetadata

    m = RelaunchMetadata.model_validate(
        {"relaunch_command": "cl", "relaunch_args": ["session", "start"], "environment": {"K": "V"}}
    )
    assert m.relaunch_command == "cl"
    assert m.relaunch_args == ["session", "start"]
    assert m.environment == {"K": "V"}
