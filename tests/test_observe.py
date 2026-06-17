# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for context_lifecycle.ledger.observe (recurrence clustering)."""

from __future__ import annotations

import datetime

from context_lifecycle.ledger.capture import LEDGER_SUBPATH
from context_lifecycle.ledger.observe import (
    DEFAULT_MIN_COUNT,
    RecurringSignal,
    find_recurring,
    observe,
)

_TODAY = datetime.date(2026, 7, 1)


def _cand(date: str, signal: str, ctx: str) -> str:
    return f"- [ ] CANDIDATE {date} {signal} — {ctx} — judgment: ___\n"


def test_no_candidates_clean():
    assert find_recurring("## Entries\n", min_count=3, window_days=30, today=_TODAY) == []


def test_below_min_count_not_clustered():
    text = _cand("2026-06-20", "sig", "a") + _cand("2026-06-21", "sig", "b")
    assert find_recurring(text, min_count=3, window_days=30, today=_TODAY) == []


def test_recurring_signal_clustered():
    text = (
        _cand("2026-06-20", "sig", "a")
        + _cand("2026-06-21", "sig", "b")
        + _cand("2026-06-22", "sig", "c")
    )
    clusters = find_recurring(text, min_count=3, window_days=30, today=_TODAY)
    assert len(clusters) == 1
    assert isinstance(clusters[0], RecurringSignal)
    assert clusters[0].count == 3
    assert clusters[0].latest_date == "2026-06-22"
    assert set(clusters[0].contexts) == {"a", "b", "c"}


def test_outside_window_not_counted():
    text = (
        _cand("2026-06-20", "sig", "a")
        + _cand("2026-06-21", "sig", "b")
        + _cand("2026-01-01", "sig", "old")  # far outside 30d
    )
    clusters = find_recurring(text, min_count=3, window_days=30, today=_TODAY)
    assert clusters == []  # only 2 in-window → below min_count


def test_sourced_signal_skipped():
    # A signal with a verifiable promoted judgment belongs to promote, not observe.
    text = (
        "- **2026-05-01** — sig: ctx → **judgment:** x [check: path:Repo:f.py]\n"
        + _cand("2026-06-20", "sig", "a")
        + _cand("2026-06-21", "sig", "b")
        + _cand("2026-06-22", "sig", "c")
    )
    assert find_recurring(text, min_count=3, window_days=30, today=_TODAY) == []


def test_unparseable_date_skipped():
    text = (
        _cand("2026-06-20", "sig", "a")
        + _cand("2026-06-21", "sig", "b")
        + "- [ ] CANDIDATE not-a-date sig — c — judgment: ___\n"
    )
    assert find_recurring(text, min_count=3, window_days=30, today=_TODAY) == []


def test_sorted_hottest_first():
    text = (
        _cand("2026-06-20", "low", "a")
        + _cand("2026-06-20", "low", "b")
        + _cand("2026-06-20", "low", "c")
        + _cand("2026-06-20", "hot", "a")
        + _cand("2026-06-20", "hot", "b")
        + _cand("2026-06-20", "hot", "c")
        + _cand("2026-06-20", "hot", "d")
    )
    clusters = find_recurring(text, min_count=3, window_days=30, today=_TODAY)
    assert [c.signal for c in clusters] == ["hot", "low"]


def test_default_min_count_is_three():
    assert DEFAULT_MIN_COUNT == 3


def test_observe_missing_file_returns_empty(tmp_path):
    assert observe(private_root=tmp_path, today=_TODAY) == []


def test_observe_reads_ledger(tmp_path):
    path = tmp_path / LEDGER_SUBPATH
    path.parent.mkdir(parents=True)
    path.write_text(
        "## Entries\n"
        + _cand("2026-06-20", "sig", "a")
        + _cand("2026-06-21", "sig", "b")
        + _cand("2026-06-22", "sig", "c"),
        encoding="utf-8",
    )
    clusters = observe(private_root=tmp_path, today=_TODAY)
    assert len(clusters) == 1
    assert clusters[0].count == 3
