"""Parity tests for evaluate_pre_tool_use against the bash hook's decision tree."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from context_lifecycle.hooks.decisions import Decision
from context_lifecycle.hooks.pre_tool_use import HookInput, evaluate_pre_tool_use
from context_lifecycle.models.config import CLConfig


def H(tool="Read", **inp):
    return HookInput(tool_name=tool, tool_input=inp)


# --- require_capsule ---

def test_require_capsule_no_capsule_blocks(paths, require_capsule_config):
    r = evaluate_pre_tool_use(paths=paths, config=require_capsule_config, hook_input=H())
    assert r.is_block
    assert "No active capsule" in r.reason


def test_require_capsule_malformed_blocks(paths, require_capsule_config, write_yaml_helper):
    # not a dict
    (paths.active / "bad.yaml").write_text("- just\n- a\n- list\n")
    r = evaluate_pre_tool_use(paths=paths, config=require_capsule_config, hook_input=H())
    assert r.is_block
    assert "invalid" in r.reason


def test_require_capsule_missing_fields_blocks(paths, require_capsule_config, write_yaml_helper):
    write_yaml_helper(paths.active / "cap.yaml", {"capsule_id": "x"})  # missing schema_version, status
    r = evaluate_pre_tool_use(paths=paths, config=require_capsule_config, hook_input=H())
    assert r.is_block
    assert "missing" in r.reason


def test_require_capsule_valid_allows(paths, require_capsule_config, write_yaml_helper, valid_capsule_data):
    write_yaml_helper(paths.active / "cap.yaml", valid_capsule_data)
    r = evaluate_pre_tool_use(paths=paths, config=require_capsule_config, hook_input=H())
    assert r.decision is Decision.ALLOW


# --- lease expiry ---

def test_lease_expired_blocks(paths, default_config, write_yaml_helper):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_yaml_helper(paths.handoffs / "h.yaml", {"lease": {"expires_at": past}})
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H())
    assert r.is_block
    assert "Lease expired" in r.reason


def test_lease_future_allows(paths, default_config, write_yaml_helper, valid_handoff_data):
    write_yaml_helper(paths.handoffs / "h.yaml", valid_handoff_data)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H())
    assert r.decision is Decision.ALLOW


def test_lease_enforce_disabled(paths, write_yaml_helper):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    write_yaml_helper(paths.handoffs / "h.yaml", {"lease": {"expires_at": past}})
    cfg = CLConfig.model_validate({"guard": {"enforce_lease": False}})
    r = evaluate_pre_tool_use(paths=paths, config=cfg, hook_input=H())
    assert r.decision is Decision.ALLOW


# --- pre_write ---

def test_pre_write_forbidden_path_blocks(paths, default_config, write_yaml_helper, valid_handoff_data):
    write_yaml_helper(paths.handoffs / "h.yaml", valid_handoff_data)
    r = evaluate_pre_tool_use(
        paths=paths, config=default_config, hook_input=H(tool="Write", file_path="secrets/key.pem")
    )
    assert r.is_block
    assert "forbidden" in r.reason


def test_pre_write_allowed_path_passes(paths, default_config, write_yaml_helper, valid_handoff_data):
    write_yaml_helper(paths.handoffs / "h.yaml", valid_handoff_data)
    r = evaluate_pre_tool_use(
        paths=paths, config=default_config, hook_input=H(tool="Write", file_path="src/main.py")
    )
    assert r.decision is Decision.ALLOW


def test_pre_write_outside_allowed_blocks(paths, default_config, write_yaml_helper, valid_handoff_data):
    write_yaml_helper(paths.handoffs / "h.yaml", valid_handoff_data)
    r = evaluate_pre_tool_use(
        paths=paths, config=default_config, hook_input=H(tool="Edit", file_path="docs/note.md")
    )
    assert r.is_block
    assert "outside worker scope" in r.reason


def test_pre_write_read_only_blocks(paths, default_config, write_yaml_helper, valid_handoff_data):
    valid_handoff_data["worker_scope"]["mutation_policy"] = "read_only"
    valid_handoff_data["worker_scope"]["allowed_paths"] = []  # allow path check past
    valid_handoff_data["worker_scope"]["forbidden_paths"] = []
    write_yaml_helper(paths.handoffs / "h.yaml", valid_handoff_data)
    r = evaluate_pre_tool_use(
        paths=paths, config=default_config, hook_input=H(tool="Write", file_path="anything.py")
    )
    assert r.is_block
    assert "read_only" in r.reason


def test_pre_write_no_handoff_allows(paths, default_config):
    r = evaluate_pre_tool_use(
        paths=paths, config=default_config, hook_input=H(tool="Write", file_path="src/x.py")
    )
    assert r.decision is Decision.ALLOW


# --- pre_spawn ---

def test_pre_spawn_max_subagents_zero_blocks(paths, default_config, write_yaml_helper, valid_handoff_data):
    valid_handoff_data["lease"]["max_subagents"] = 0
    write_yaml_helper(paths.handoffs / "h.yaml", valid_handoff_data)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H(tool="Agent"))
    assert r.is_block
    assert "prohibits subagent" in r.reason


def test_pre_spawn_high_parallelism_blocks(paths, default_config, write_yaml_helper, valid_checkpoint_data):
    valid_checkpoint_data["orchestrator"]["context_risk"]["high_parallelism"] = True
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H(tool="Agent"))
    assert r.is_block
    assert "high_parallelism" in r.reason


def test_pre_spawn_subagent_heavy_warns(paths, default_config, write_yaml_helper, valid_checkpoint_data):
    valid_checkpoint_data["orchestrator"]["context_risk"]["subagent_heavy"] = True
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H(tool="Agent"))
    assert r.decision is Decision.ALLOW
    assert any("subagent_heavy" in w.reason for w in r.warnings)


# --- context_risk flags ---

def test_checkpoint_stale_blocks(paths, default_config, write_yaml_helper, valid_checkpoint_data):
    valid_checkpoint_data["orchestrator"]["context_risk"]["checkpoint_stale"] = True
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H(tool="Read"))
    assert r.is_block
    assert "checkpoint_stale" in r.reason


def test_long_lived_session_warns(paths, default_config, write_yaml_helper, valid_checkpoint_data):
    valid_checkpoint_data["orchestrator"]["context_risk"]["long_lived_session"] = True
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H(tool="Read"))
    assert r.decision is Decision.ALLOW
    assert any("long_lived_session" in w.reason for w in r.warnings)


def test_reload_scope_too_large_warns_on_read(paths, default_config, write_yaml_helper, valid_checkpoint_data):
    valid_checkpoint_data["orchestrator"]["context_risk"]["reload_scope_too_large"] = True
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H(tool="Read"))
    assert any("reload_scope_too_large" in w.reason for w in r.warnings)


def test_reload_scope_too_large_silent_on_write(paths, default_config, write_yaml_helper, valid_checkpoint_data):
    valid_checkpoint_data["orchestrator"]["context_risk"]["reload_scope_too_large"] = True
    write_yaml_helper(paths.checkpoints / "c.yaml", valid_checkpoint_data)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H(tool="Write", file_path="src/x"))
    assert not any("reload_scope_too_large" in w.reason for w in r.warnings)


def test_latest_checkpoint_used(paths, default_config, write_yaml_helper, valid_checkpoint_data):
    # older checkpoint clean, newer flips checkpoint_stale → block
    early = dict(valid_checkpoint_data)
    early["checkpoint_id"] = "a-early"
    write_yaml_helper(paths.checkpoints / "2026-05-22T10-00-00Z.yaml", early)
    late = {
        "checkpoint_id": "z-late",
        "schema_version": "0.1",
        "orchestrator": {"context_risk": {"checkpoint_stale": True}},
    }
    write_yaml_helper(paths.checkpoints / "2026-05-22T11-00-00Z.yaml", late)
    r = evaluate_pre_tool_use(paths=paths, config=default_config, hook_input=H(tool="Read"))
    assert r.is_block
