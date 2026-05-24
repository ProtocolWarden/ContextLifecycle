"""CLConfig model default/override tests."""

from __future__ import annotations

from context_lifecycle.models.config import CLConfig


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


def test_guard_config_path_defaults():
    from context_lifecycle.models.config import GuardConfig

    g = GuardConfig()
    assert g.capsule_path == ".context/active/"
    assert g.checkpoint_path == ".context/checkpoints/"
    assert g.handoff_path == ".context/handoffs/"


def test_loop_config_default():
    from context_lifecycle.models.config import LoopConfig

    assert LoopConfig().checkpoint_on_stop is True
