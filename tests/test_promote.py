# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Unit tests for context_lifecycle.ledger.promote (self-verifying promotion)."""

from __future__ import annotations

import datetime

from context_lifecycle.ledger.capture import LEDGER_SUBPATH
from context_lifecycle.ledger.promote import (
    PromoteOutcome,
    auto_promote,
    promote,
    verify_ref,
)

_TODAY = datetime.date(2026, 7, 1)

_ALWAYS_OK = lambda ref: (True, f"ok:{ref}")  # noqa: E731
_ALWAYS_BAD = lambda ref: (False, f"missing:{ref}")  # noqa: E731


def _cand(date: str, signal: str, ctx: str) -> str:
    return f"- [ ] CANDIDATE {date} {signal} — {ctx} — judgment: ___\n"


def _source(date: str, signal: str, ref: str) -> str:
    return f"- **{date}** — {signal}: operator did x → **judgment:** y [check: {ref}]\n"


def test_no_source_judgment_leaves_candidate(tmp_path=None):
    text = _cand("2026-06-20", "sig", "a")
    out = auto_promote(text, verify=_ALWAYS_OK, today=_TODAY)
    assert isinstance(out, PromoteOutcome)
    assert out.promoted == []
    assert out.new_text == text  # untouched — no judgment to inherit


def test_verifiable_recurrence_is_promoted():
    text = _source("2026-05-01", "sig", "path:Repo:f.py") + _cand("2026-06-20", "sig", "a")
    out = auto_promote(text, verify=_ALWAYS_OK, today=_TODAY)
    assert len(out.promoted) == 1
    assert out.promoted[0][0] == "sig"
    assert out.changed is True
    # candidate marker gone; reconfirmation line written citing the source date
    assert "- [ ] CANDIDATE" not in out.new_text
    assert "auto-reconfirmed against 2026-05-01" in out.new_text
    assert "[reconfirmed: path:Repo:f.py @ 2026-07-01]" in out.new_text


def test_regressed_check_refuses_to_promote():
    text = _source("2026-05-01", "sig", "path:Repo:gone.py") + _cand("2026-06-20", "sig", "a")
    out = auto_promote(text, verify=_ALWAYS_BAD, today=_TODAY)
    assert out.promoted == []
    assert len(out.regressed) == 1
    assert out.regressed[0][0] == "sig"
    assert "- [ ] CANDIDATE 2026-06-20 sig" in out.new_text  # candidate untouched


def test_auto_line_does_not_become_a_source():
    # An auto-promoted line uses [reconfirmed: …], NOT [check: …], so it must not
    # serve as a source judgment for a *second* candidate of the same signal.
    text = _source("2026-05-01", "sig", "path:Repo:f.py") + _cand("2026-06-20", "sig", "a")
    once = auto_promote(text, verify=_ALWAYS_OK, today=_TODAY)
    # Add a fresh candidate, drop the original [check:] source, keep only the auto line.
    auto_only = "\n".join(
        ln for ln in once.new_text.splitlines() if "[check:" not in ln
    ) + "\n" + _cand("2026-06-25", "sig", "b")
    twice = auto_promote(auto_only, verify=_ALWAYS_OK, today=_TODAY)
    assert twice.promoted == []  # no [check:] source remains → nothing self-promotes


def test_most_recent_source_ref_wins():
    text = (
        _source("2026-05-01", "sig", "path:Repo:old.py")
        + _source("2026-06-01", "sig", "path:Repo:new.py")
        + _cand("2026-06-20", "sig", "a")
    )
    out = auto_promote(text, verify=_ALWAYS_OK, today=_TODAY)
    assert out.promoted[0][1] == "path:Repo:new.py"


# --- verify_ref: real file-read verification --------------------------------


def test_verify_path_ref(tmp_path):
    (tmp_path / "Repo").mkdir()
    (tmp_path / "Repo" / "f.py").write_text("x", encoding="utf-8")
    assert verify_ref("path:Repo:f.py", repos_root=tmp_path)[0] is True
    assert verify_ref("path:Repo:nope.py", repos_root=tmp_path)[0] is False


def test_verify_custodian_ref(tmp_path):
    (tmp_path / "Repo").mkdir()
    (tmp_path / "Repo" / ".custodian.yaml").write_text("detectors:\n  - OC3\n", encoding="utf-8")
    assert verify_ref("custodian:Repo:OC3", repos_root=tmp_path)[0] is True
    assert verify_ref("custodian:Repo:OC9", repos_root=tmp_path)[0] is False
    # missing config file → not ok
    assert verify_ref("custodian:Other:OC3", repos_root=tmp_path)[0] is False


def test_verify_ci_ref(tmp_path):
    wf = tmp_path / "Repo" / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "validate.yml").write_text("jobs:\n  custodian:\n    runs-on: x\n", encoding="utf-8")
    assert verify_ref("ci:Repo:validate.yml:custodian", repos_root=tmp_path)[0] is True
    assert verify_ref("ci:Repo:validate.yml:absent", repos_root=tmp_path)[0] is False


def test_verify_unknown_ref_is_false(tmp_path):
    assert verify_ref("bogus:thing", repos_root=tmp_path)[0] is False


def test_promote_missing_file_returns_empty(tmp_path):
    out = promote(repos_root=tmp_path, private_root=tmp_path, today=_TODAY)
    assert out.promoted == [] and out.regressed == []


def test_promote_writes_back(tmp_path):
    repos = tmp_path / "repos"
    (repos / "Repo").mkdir(parents=True)
    (repos / "Repo" / "f.py").write_text("x", encoding="utf-8")
    private = tmp_path / "private"
    path = private / LEDGER_SUBPATH
    path.parent.mkdir(parents=True)
    path.write_text(
        "## Entries\n"
        + _source("2026-05-01", "sig", "path:Repo:f.py")
        + _cand("2026-06-20", "sig", "a"),
        encoding="utf-8",
    )
    out = promote(repos_root=repos, private_root=private, today=_TODAY)
    assert len(out.promoted) == 1
    assert "auto-reconfirmed" in path.read_text(encoding="utf-8")
