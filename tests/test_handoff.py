# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""WorkerHandoff model + lease-expiry tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from context_lifecycle.models.handoff import WorkerHandoff


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


def test_worker_scope_defaults_and_validation():
    from context_lifecycle.models.handoff import WorkerScope

    s = WorkerScope.model_validate(
        {"repo": "R", "allowed_paths": ["src/"], "mutation_policy": "write_allowed"}
    )
    assert s.repo == "R"
    assert s.allowed_paths == ["src/"]
    assert s.mutation_policy == "write_allowed"


def test_lease_max_subagents_sentinel():
    from context_lifecycle.models.handoff import Lease

    assert Lease().max_subagents == -1  # -1 = unset
    assert Lease.model_validate({"max_subagents": 0}).max_subagents == 0
