# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""DecisionResult / Allow / Block / Warn behavior tests."""

from __future__ import annotations

from context_lifecycle.hooks.decisions import (
    Allow,
    Block,
    Decision,
    DecisionResult,
    Warn,
)


def test_allow_is_not_block():
    result = Allow()
    assert result.decision is Decision.ALLOW
    assert result.is_block is False
    assert result.reason == ""
    assert result.warnings == []


def test_block_helper_constructs_block():
    result = Block("nope")
    assert result.is_block is True
    assert result.reason == "nope"


def test_block_first_wins():
    result = Allow()
    result.block("first")
    result.block("second")
    assert result.reason == "first"
    assert result.is_block is True


def test_warn_accumulates_without_blocking():
    result = Allow()
    result.warn("careful")
    result.warn("again")
    assert result.is_block is False
    assert [w.reason for w in result.warnings] == ["careful", "again"]
    assert all(isinstance(w, Warn) for w in result.warnings)


def test_block_chains_return_self():
    result = DecisionResult()
    assert result.block("x") is result
    assert result.warn("y") is result
