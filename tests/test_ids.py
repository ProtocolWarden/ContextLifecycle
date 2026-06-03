# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from context_lifecycle.errors import SessionNotStarted
from context_lifecycle.session.ids import (
    ENV_VAR,
    generate_session_id,
    is_valid_session_id,
    require_session_env,
)


def test_generate_session_id_format():
    sid = generate_session_id(now=datetime(2026, 5, 22, tzinfo=timezone.utc))
    assert sid.startswith("s-2026-05-22-")
    assert is_valid_session_id(sid)


def test_session_id_uniqueness():
    ids = {generate_session_id() for _ in range(50)}
    # extremely unlikely to collide
    assert len(ids) >= 49


def test_require_session_env_missing(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(SessionNotStarted):
        require_session_env()


def test_require_session_env_present(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "s-2026-05-22-abcd")
    assert require_session_env() == "s-2026-05-22-abcd"


def test_is_valid_session_id_rejects_bad():
    assert not is_valid_session_id("nope")
    assert not is_valid_session_id("s-2026-5-22-abcd")
    assert not is_valid_session_id("s-2026-05-22-ABCD")
