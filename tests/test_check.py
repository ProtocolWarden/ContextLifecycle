# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for context_lifecycle.ledger.check (staleness of candidates)."""

from __future__ import annotations

import datetime

from context_lifecycle.ledger.capture import LEDGER_SUBPATH
from context_lifecycle.ledger.check import (
    DEFAULT_MAX_AGE_DAYS,
    StaleCandidate,
    check,
    find_stale_candidates,
)

_TODAY = datetime.date(2026, 7, 1)


def _candidate(date: str, signal: str = "sig", ctx: str = "ctx") -> str:
    return f"- [ ] CANDIDATE {date} {signal} — {ctx} — judgment: ___\n"


def test_no_candidates_clean():
    assert find_stale_candidates("## Entries\n", max_age_days=14, today=_TODAY) == []


def test_old_candidate_flagged():
    text = _candidate("2026-06-01")  # 30 days before _TODAY
    stale = find_stale_candidates(text, max_age_days=14, today=_TODAY)
    assert len(stale) == 1
    assert isinstance(stale[0], StaleCandidate)
    assert stale[0].age_days == 30


def test_recent_candidate_not_flagged():
    text = _candidate("2026-06-25")  # 6 days
    assert find_stale_candidates(text, max_age_days=14, today=_TODAY) == []


def test_boundary_exactly_threshold_not_flagged():
    # age == max_age_days is NOT past the threshold (strictly greater).
    text = _candidate("2026-06-17")  # 14 days
    assert find_stale_candidates(text, max_age_days=14, today=_TODAY) == []


def test_promoted_entry_never_flagged():
    # Promotion removes the CANDIDATE marker → must not be parsed as a candidate.
    text = "- **2026-01-01** — ancient → **judgment:** do x\n"
    assert find_stale_candidates(text, max_age_days=14, today=_TODAY) == []


def test_unparseable_date_skipped():
    text = "- [ ] CANDIDATE not-a-date sig — ctx — judgment: ___\n"
    assert find_stale_candidates(text, max_age_days=14, today=_TODAY) == []


def test_sorted_oldest_first():
    text = _candidate("2026-06-10", "a") + _candidate("2026-06-01", "b")
    stale = find_stale_candidates(text, max_age_days=5, today=_TODAY)
    assert [c.signal for c in stale] == ["b", "a"]  # b is older


def test_check_missing_file_returns_empty(tmp_path):
    # Private root resolves but no ledger file yet → nothing to check.
    assert check(private_root=tmp_path, today=_TODAY) == []


def test_check_reads_ledger(tmp_path):
    path = tmp_path / LEDGER_SUBPATH
    path.parent.mkdir(parents=True)
    path.write_text("## Entries\n" + _candidate("2026-01-01"), encoding="utf-8")
    stale = check(private_root=tmp_path, today=_TODAY, max_age_days=DEFAULT_MAX_AGE_DAYS)
    assert len(stale) == 1
