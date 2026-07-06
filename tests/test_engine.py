# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""PseudoOperator harness (Track B): config fail-closed, atomic locking,
enforced caps, delay policies, limit parsing, hooks, pause semantics."""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from context_lifecycle.pseudo_operator import (
    PseudoOperatorEngine,
    load_pseudo_operator_config,
)
from context_lifecycle.pseudo_operator.config import PseudoOperatorConfig
from context_lifecycle.pseudo_operator import engine as engine_mod

assert engine_mod is not None  # T6: module import reference


def _cfg(tmp_path: Path, **overrides) -> PseudoOperatorConfig:
    (tmp_path / "prompt.txt").write_text("do the thing")
    base = dict(
        loop_name="test",
        repo_root=tmp_path,
        session_prompt_file="prompt.txt",
        max_iterations=3,
        max_consecutive_failures=2,
        session_timeout_seconds=5,
    )
    base.update(overrides)
    return PseudoOperatorConfig.model_validate(base)


# ── config ────────────────────────────────────────────────────────────────


def test_config_loads_pseudo_operator_section(tmp_path: Path):
    ctx = tmp_path / "repo" / ".context"
    ctx.mkdir(parents=True)
    (tmp_path / "repo" / "p.txt").write_text("x")
    cfg_file = ctx / "config.yaml"
    cfg_file.write_text(
        "clp_version: '0.1'\n"
        "pseudo_operator:\n"
        "  loop_name: vf\n"
        "  session_prompt_file: p.txt\n"
        "  delay:\n"
        "    kind: phase_status\n"
        "    default_delay_seconds: 270\n"
        "    phase_delays: {authoring: 2700, rendering: 1200}\n"
        "    status_glob: 'reports/*'\n"
    )
    cfg = load_pseudo_operator_config(cfg_file)
    assert cfg.loop_name == "vf"
    # repo_root inferred as the dir CONTAINING .context/
    assert cfg.repo_root == (tmp_path / "repo").resolve()
    assert cfg.delay.phase_delays["authoring"] == 2700
    # guardrail caps have safe defaults — never unlimited
    assert cfg.max_iterations > 0 and cfg.max_consecutive_failures > 0


def test_config_missing_section_fails_closed(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text("clp_version: '0.1'\nrepo: X\n")
    with pytest.raises(ValueError, match="refuses to start"):
        load_pseudo_operator_config(f)


def test_config_unknown_key_rejected(tmp_path: Path):
    f = tmp_path / "config.yaml"
    f.write_text(
        "pseudo_operator:\n  loop_name: t\n  session_prompt_file: p\n  max_iterationz: 5\n"
    )
    with pytest.raises(Exception, match="max_iterationz"):
        load_pseudo_operator_config(f)


def test_config_nonpositive_caps_rejected(tmp_path: Path):
    with pytest.raises(Exception, match="positive"):
        _cfg(tmp_path, max_iterations=0)


# ── locking ───────────────────────────────────────────────────────────────


def test_lock_acquire_release(tmp_path: Path):
    eng = PseudoOperatorEngine(_cfg(tmp_path))
    assert eng.acquire_lock() is True
    d = json.loads(eng.cfg.lock_path.read_text())
    assert d["pid"] == os.getpid()
    assert d["hostname"] == socket.gethostname()
    eng.release_lock()
    assert not eng.cfg.lock_path.exists()


def test_lock_held_by_live_pid_aborts(tmp_path: Path):
    eng = PseudoOperatorEngine(_cfg(tmp_path))
    assert eng.acquire_lock() is True  # our own live pid holds it
    eng2 = PseudoOperatorEngine(_cfg(tmp_path))
    assert eng2.acquire_lock() is False
    eng.release_lock()


def test_stale_lock_reclaimed_same_host(tmp_path: Path):
    eng = PseudoOperatorEngine(_cfg(tmp_path))
    eng.cfg.lock_path.write_text(
        json.dumps({"pid": 2**22 + 12345, "hostname": socket.gethostname(), "started": "x"})
    )
    assert eng.acquire_lock() is True
    eng.release_lock()


def test_cross_host_lock_treated_as_held(tmp_path: Path):
    eng = PseudoOperatorEngine(_cfg(tmp_path))
    eng.cfg.lock_path.write_text(
        json.dumps({"pid": 1, "hostname": "some-other-host", "started": "x"})
    )
    assert eng.acquire_lock() is False


# ── caps + loop behavior (run_session mocked) ─────────────────────────────


def _run_engine(tmp_path: Path, session_results, monkeypatch, **cfg_overrides):
    eng = PseudoOperatorEngine(_cfg(tmp_path, **cfg_overrides))
    calls = {"n": 0}

    def fake_session(iteration, backend, anchor_vars):
        calls["n"] += 1
        rc = session_results[min(calls["n"] - 1, len(session_results) - 1)]
        log = tmp_path / f"session_{calls['n']}.log"
        log.write_text("")
        return rc, log

    monkeypatch.setattr(eng, "run_session", fake_session)
    monkeypatch.setattr(eng, "_anchor_via_cl", lambda env: None)
    monkeypatch.setattr(eng, "_end_cl_session", lambda av: None)
    monkeypatch.setattr(eng, "interruptible_sleep", lambda s, **kw: None)
    monkeypatch.setattr(
        "context_lifecycle.pseudo_operator.engine._command_available", lambda c: True
    )
    rcode = eng.run()
    return eng, calls["n"], rcode


def test_max_iterations_cap_enforced(tmp_path: Path, monkeypatch):
    _, sessions, rc = _run_engine(tmp_path, [0], monkeypatch, max_iterations=3)
    assert rc == 0
    assert sessions == 3  # stopped by the cap, not unbounded


def test_consecutive_failure_cap_enforced(tmp_path: Path, monkeypatch):
    _, sessions, rc = _run_engine(
        tmp_path, [1], monkeypatch, max_iterations=50, max_consecutive_failures=2
    )
    assert sessions == 2  # two non-rate-limit failures → guard stops the loop


def test_success_resets_failure_counter(tmp_path: Path, monkeypatch):
    # fail, succeed, fail, succeed... never two consecutive → cap never fires;
    # the iteration cap ends the loop instead.
    _, sessions, _ = _run_engine(
        tmp_path, [1, 0, 1, 0, 1, 0], monkeypatch, max_iterations=6, max_consecutive_failures=2
    )
    assert sessions == 6


def test_pause_flag_idles_without_sessions(tmp_path: Path, monkeypatch):
    cfg = _cfg(tmp_path, max_iterations=3)
    eng = PseudoOperatorEngine(cfg)
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    cfg.pause_flag_path.touch()
    ran = {"n": 0}
    monkeypatch.setattr(eng, "run_session", lambda *a, **k: ran.__setitem__("n", ran["n"] + 1))
    monkeypatch.setattr(eng, "_anchor_via_cl", lambda env: None)
    monkeypatch.setattr(eng, "_end_cl_session", lambda av: None)

    def fake_sleep(seconds, **kw):
        # First paused idle: un-pause and stop, so run() exits promptly.
        eng._stop = True

    monkeypatch.setattr(eng, "interruptible_sleep", fake_sleep)
    monkeypatch.setattr(
        "context_lifecycle.pseudo_operator.engine._command_available", lambda c: True
    )
    assert eng.run() == 0
    assert ran["n"] == 0  # never spawned a session while paused


# ── delay policies ────────────────────────────────────────────────────────


def test_schedule_override_wins(tmp_path: Path):
    eng = PseudoOperatorEngine(_cfg(tmp_path))
    eng.cfg.state_path.mkdir(parents=True, exist_ok=True)
    eng.cfg.schedule_path.write_text(json.dumps({"delay_s": 42, "reason": "test"}))
    assert eng.get_delay() == 42


def test_state_delay_policy(tmp_path: Path):
    eng = PseudoOperatorEngine(
        _cfg(
            tmp_path,
            delay={
                "kind": "schedule_state",
                "default_delay_seconds": 600,
                "state_delays": {"STALLED": 120},
            },
        )
    )
    eng.cfg.state_path.mkdir(parents=True, exist_ok=True)
    eng.cfg.schedule_path.write_text(json.dumps({"state": "STALLED"}))
    assert eng.get_delay() == 120


def test_phase_delay_policy(tmp_path: Path):
    reports = tmp_path / "reports" / "run1"
    reports.mkdir(parents=True)
    (reports / "run_status.json").write_text(
        json.dumps({"status": "in_progress", "current_phase": "authoring"})
    )
    eng = PseudoOperatorEngine(
        _cfg(
            tmp_path,
            delay={
                "kind": "phase_status",
                "default_delay_seconds": 270,
                "phase_delays": {"authoring": 2700},
                "status_glob": "reports/*",
            },
        )
    )
    assert eng.get_delay() == 2700


def test_fixed_delay_default(tmp_path: Path):
    eng = PseudoOperatorEngine(_cfg(tmp_path, delay={"kind": "fixed", "default_delay_seconds": 99}))
    assert eng.get_delay() == 99


def test_global_claude_limit_cools_all_claude_backends(tmp_path: Path):
    eng = PseudoOperatorEngine(_cfg(tmp_path))
    log = tmp_path / "s.log"
    log.write_text("You have hit your 5-hour session limit. Try again in 1h.")
    cooldowns = {"claude": None, "opus": None, "codex": None}
    assert eng._handle_backend_limit("claude", log, cooldowns) is True
    assert cooldowns["claude"] is not None
    assert cooldowns["opus"] is not None  # claude-CLI sibling also cooled
    assert cooldowns["codex"] is None




# ── hooks ─────────────────────────────────────────────────────────────────


def test_seed_cooldowns_hook(tmp_path: Path):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    script = tmp_path / "seed.py"
    script.write_text(f"import json; print(json.dumps({{'claude': '{future}', 'codex': None}}))")
    eng = PseudoOperatorEngine(
        _cfg(tmp_path, hooks={"seed_cooldowns": ["python3", str(script)]})
    )
    cooldowns = {"claude": None, "opus": None, "codex": None}
    eng._seed_cooldowns(cooldowns)
    assert cooldowns["claude"] is not None
    assert cooldowns["codex"] is None


def test_on_cooldown_hook_receives_json(tmp_path: Path):
    out_file = tmp_path / "hook_out.json"
    script = tmp_path / "hook.py"
    script.write_text(
        "import sys, pathlib; pathlib.Path(sys.argv[2]).write_text(sys.argv[1])"
        if False
        else f"import sys, pathlib\npathlib.Path({str(out_file)!r}).write_text(sys.argv[1])\n"
    )
    eng = PseudoOperatorEngine(
        _cfg(tmp_path, hooks={"on_cooldown": ["python3", str(script)]})
    )
    log = tmp_path / "s.log"
    log.write_text("usage limit — resets at 2027-01-02T03:04Z")
    cooldowns = {"claude": None, "opus": None, "codex": None}
    eng._handle_backend_limit("claude", log, cooldowns)
    payload = json.loads(out_file.read_text())
    assert payload["backend"] == "claude"
    assert payload["reset_at"].startswith("2027-01-02")
    assert payload["limit_kind"]
