# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Fail-closed CI-status resolver — the §9 sha → tests_green half of D3.

Exercises the whole doubt matrix behind a mocked network seam (``_run_gh``):
green ⇒ True, any failure ⇒ False, and every ambiguity (in-flight run, empty
set, missing token, non-zero exit, malformed JSON, raised exception) ⇒ the
verbatim ``"unknown"`` cold.py stores — never True on doubt, never raises.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from context_lifecycle.context_engine import ci_status
from context_lifecycle.context_engine.ci_status import CIStatus, resolve_ci_status

_OWNER, _REPO, _SHA = "ProtocolWarden", "ContextLifecycle", "a" * 40
_TOKEN = "gho_faketoken"


def _run(cr: dict, id_: int = 1, name: str = "check") -> dict:
    """Build a single check-run dict with sensible defaults."""
    return {"id": id_, "name": name, **cr}


def _completed(payload: object, returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess[str]:
    stdout = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def _patch_runs(monkeypatch, runs: list[dict]) -> None:
    """Make the network seam return a check-runs payload with these runs."""
    monkeypatch.setattr(
        ci_status,
        "_run_gh",
        lambda args, *, token, timeout: _completed({"total_count": len(runs), "check_runs": runs}),
    )


def _patch_raw(monkeypatch, proc: subprocess.CompletedProcess[str]) -> None:
    monkeypatch.setattr(ci_status, "_run_gh", lambda args, *, token, timeout: proc)


# ── green ⇒ True ──────────────────────────────────────────────────────────────
def test_all_completed_success_is_true(monkeypatch):
    _patch_runs(
        monkeypatch,
        [
            _run({"status": "completed", "conclusion": "success"}, id_=1, name="lint"),
            _run({"status": "completed", "conclusion": "success"}, id_=2, name="test"),
        ],
    )
    result = resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN)
    assert isinstance(result, CIStatus)
    assert result.tests_green is True
    assert result.resolved is True


def test_neutral_and_skipped_still_green(monkeypatch):
    _patch_runs(
        monkeypatch,
        [
            _run({"status": "completed", "conclusion": "success"}, id_=1, name="a"),
            _run({"status": "completed", "conclusion": "neutral"}, id_=2, name="b"),
            _run({"status": "completed", "conclusion": "skipped"}, id_=3, name="c"),
        ],
    )
    assert resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN).tests_green is True


def test_stale_failure_deduped_by_newest_run_is_green(monkeypatch):
    # Older failing run (low id) superseded by a newer success on the same name.
    _patch_runs(
        monkeypatch,
        [
            _run({"status": "completed", "conclusion": "failure"}, id_=1, name="test"),
            _run({"status": "completed", "conclusion": "success"}, id_=2, name="test"),
        ],
    )
    assert resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN).tests_green is True


# ── failure ⇒ False ───────────────────────────────────────────────────────────
def test_one_failure_is_false(monkeypatch):
    _patch_runs(
        monkeypatch,
        [
            _run({"status": "completed", "conclusion": "success"}, id_=1, name="lint"),
            _run({"status": "completed", "conclusion": "failure"}, id_=2, name="test"),
        ],
    )
    result = resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN)
    assert result.tests_green is False
    assert result.resolved is True


@pytest.mark.parametrize("bad", ["timed_out", "cancelled", "action_required", "stale"])
def test_terminal_failing_conclusions_are_false(monkeypatch, bad):
    _patch_runs(monkeypatch, [_run({"status": "completed", "conclusion": bad})])
    assert resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN).tests_green is False


def test_failure_wins_even_with_an_incomplete_run(monkeypatch):
    # A definitive failure is reported as False even if another run is pending.
    _patch_runs(
        monkeypatch,
        [
            _run({"status": "completed", "conclusion": "failure"}, id_=1, name="test"),
            _run({"status": "in_progress", "conclusion": None}, id_=2, name="build"),
        ],
    )
    assert resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN).tests_green is False


# ── doubt ⇒ "unknown" (never True) ────────────────────────────────────────────
@pytest.mark.parametrize("status", ["in_progress", "queued"])
def test_incomplete_run_is_unknown(monkeypatch, status):
    _patch_runs(
        monkeypatch,
        [
            _run({"status": "completed", "conclusion": "success"}, id_=1, name="lint"),
            _run({"status": status, "conclusion": None}, id_=2, name="test"),
        ],
    )
    result = resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN)
    assert result.tests_green == "unknown"
    assert result.resolved is False


def test_empty_check_runs_is_unknown(monkeypatch):
    _patch_runs(monkeypatch, [])
    result = resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN)
    assert result.tests_green == "unknown"
    assert result.resolved is False


def test_completed_but_null_conclusion_is_unknown(monkeypatch):
    # Completed with no/unmodelled conclusion is doubt, not green.
    _patch_runs(monkeypatch, [_run({"status": "completed", "conclusion": None})])
    assert resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN).tests_green == "unknown"


def test_missing_token_short_circuits_to_unknown(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("network seam must not be invoked without a token")

    monkeypatch.setattr(ci_status, "_run_gh", _boom)
    # No token supplied and the module never reaches into the ambient env.
    result = resolve_ci_status(_OWNER, _REPO, _SHA, token=None)
    assert result.tests_green == "unknown"
    assert result.resolved is False


def test_nonzero_exit_is_unknown(monkeypatch):
    # e.g. unknown sha (404) or auth failure — gh exits non-zero.
    _patch_raw(monkeypatch, _completed("", returncode=1, stderr="gh: Not Found (HTTP 404)"))
    result = resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN)
    assert result.tests_green == "unknown"
    assert result.resolved is False


def test_malformed_json_is_unknown(monkeypatch):
    _patch_raw(monkeypatch, _completed("not-json{{{", returncode=0))
    assert resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN).tests_green == "unknown"


def test_check_runs_not_a_list_is_unknown(monkeypatch):
    monkeypatch.setattr(
        ci_status, "_run_gh", lambda args, *, token, timeout: _completed({"check_runs": "nope"})
    )
    assert resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN).tests_green == "unknown"


def test_seam_raising_is_unknown_never_raises(monkeypatch):
    def _raise(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="gh", timeout=30)

    monkeypatch.setattr(ci_status, "_run_gh", _raise)
    # Must not propagate — fail-closed to unknown.
    result = resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN)
    assert result.tests_green == "unknown"
    assert result.resolved is False


def test_blank_arguments_are_unknown(monkeypatch):
    def _boom(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("must not query GitHub with blank args")

    monkeypatch.setattr(ci_status, "_run_gh", _boom)
    assert resolve_ci_status("", _REPO, _SHA, token=_TOKEN).tests_green == "unknown"
    assert resolve_ci_status(_OWNER, "", _SHA, token=_TOKEN).tests_green == "unknown"
    assert resolve_ci_status(_OWNER, _REPO, "", token=_TOKEN).tests_green == "unknown"


def test_result_never_leaks_a_non_contract_value(monkeypatch):
    # Whatever the input, tests_green is exactly True / False / "unknown".
    for runs in ([], [_run({"status": "completed", "conclusion": "success"})],
                 [_run({"status": "queued", "conclusion": None})],
                 [_run({"status": "completed", "conclusion": "failure"})]):
        _patch_runs(monkeypatch, runs)
        tg = resolve_ci_status(_OWNER, _REPO, _SHA, token=_TOKEN).tests_green
        assert tg is True or tg is False or tg == "unknown"
