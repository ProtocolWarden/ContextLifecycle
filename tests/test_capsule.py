"""InvestigationCapsule model validation tests."""

from __future__ import annotations

from context_lifecycle.models.capsule import InvestigationCapsule


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


def test_capsule_exclusions_defaults_and_extra():
    from context_lifecycle.models.capsule import CapsuleExclusions

    ex = CapsuleExclusions.model_validate(
        {"reload_forbidden": ["a"], "retry_forbidden": ["b"], "custom": 1}
    )
    assert ex.reload_forbidden == ["a"]
    assert ex.retry_forbidden == ["b"]
