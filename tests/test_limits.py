# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Rate-limit parsing + classification for the PseudoOperator harness."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from context_lifecycle.pseudo_operator import limits
from context_lifecycle.pseudo_operator.limits import (
    classify_limit_kind,
    parse_rate_limit_reset,
)

assert limits is not None  # T6: module import reference

# ── limits ────────────────────────────────────────────────────────────────


def test_parse_relative_reset(tmp_path: Path):
    log = tmp_path / "s.log"
    log.write_text("Rate limit reached. Try again in 2h 30m.")
    reset, text = parse_rate_limit_reset(log)
    assert reset is not None
    assert timedelta(hours=2) < (reset - datetime.now(UTC)) <= timedelta(hours=2, minutes=31)


def test_parse_iso_reset(tmp_path: Path):
    log = tmp_path / "s.log"
    log.write_text("usage limit — resets at 2027-01-02T03:04Z")
    reset, _ = parse_rate_limit_reset(log)
    assert reset == datetime(2027, 1, 2, 3, 4, tzinfo=UTC)


def test_no_limit_signal_returns_none(tmp_path: Path):
    log = tmp_path / "s.log"
    log.write_text("session completed fine")
    assert parse_rate_limit_reset(log) == (None, None)


def test_classify_limit_kinds():
    assert classify_limit_kind("you hit your 5-hour session limit", model="m")[0] == "session_5h"
    assert classify_limit_kind("organization limit reached", model="m")[0] == "global_weekly"
    assert classify_limit_kind("weekly limit reached", model="m")[0] == "global_weekly"
    assert classify_limit_kind("sonnet weekly limit", model="m") == ("model_weekly", "m")
    assert classify_limit_kind(None, model="m") == ("model_weekly", "m")


