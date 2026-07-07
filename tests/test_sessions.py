# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Session layer of the PseudoOperator harness: command shapes, env, resolution."""

from __future__ import annotations

from pathlib import Path

from context_lifecycle.pseudo_operator import sessions
from context_lifecycle.pseudo_operator.config import (
    BackendSpec,
    DelayPolicy,
    HookCommands,
    PseudoOperatorConfig,
)
from context_lifecycle.pseudo_operator.engine import PseudoOperatorEngine
from context_lifecycle.pseudo_operator.sessions import SessionMixin

assert sessions is not None and SessionMixin is not None  # T6/T1 references


def _engine(tmp_path: Path) -> PseudoOperatorEngine:
    (tmp_path / "prompt.txt").write_text("p", encoding="utf-8")
    cfg = PseudoOperatorConfig(
        loop_name="t",
        repo_root=tmp_path,
        session_prompt_file=Path("prompt.txt"),
        delay=DelayPolicy(kind="fixed", default_delay_seconds=1),
        hooks=HookCommands(),
    )
    return PseudoOperatorEngine(cfg)


def test_claude_command_shape(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sessions, "_resolve_command", lambda c: f"/bin/{c}")
    eng = _engine(tmp_path)
    cmd = eng._session_command("claude", "PROMPT")
    assert cmd[0] == "/bin/claude"
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "claude-sonnet-5"
    assert "--dangerously-skip-permissions" in cmd
    assert cmd[cmd.index("-p") + 1] == "PROMPT"


def test_opus_uses_claude_cli_with_opus_model(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sessions, "_resolve_command", lambda c: f"/bin/{c}")
    eng = _engine(tmp_path)
    cmd = eng._session_command("opus", "PROMPT")
    assert cmd[0] == "/bin/claude"
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"


def test_codex_command_shape(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sessions, "_resolve_command", lambda c: f"/bin/{c}")
    eng = _engine(tmp_path)
    cmd = eng._session_command("codex", "PROMPT")
    assert cmd[0] == "/bin/codex"
    assert "exec" in cmd
    assert cmd[cmd.index("--model") + 1] == "gpt-5.4"
    assert cmd[-1] == "PROMPT"


def test_unknown_backend_kind_rejected():
    import pytest

    spec = BackendSpec(name="x", kind="weird", model="m")
    with pytest.raises(ValueError, match="invocation kind"):
        _ = spec.invocation_kind


def test_env_file_sourced_without_overriding(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sessions, "_resolve_command", lambda c: None)
    env_file = tmp_path / ".env.local"
    env_file.write_text("export FOO=bar\nBAZ='qux'\n# comment\n", encoding="utf-8")
    monkeypatch.setenv("BAZ", "preexisting")
    (tmp_path / "prompt.txt").write_text("p", encoding="utf-8")
    cfg = PseudoOperatorConfig(
        loop_name="t",
        repo_root=tmp_path,
        session_prompt_file=Path("prompt.txt"),
        env_file=Path(".env.local"),
    )
    eng = PseudoOperatorEngine(cfg)
    env = eng._session_env("claude", {})
    assert env["FOO"] == "bar"
    assert env["BAZ"] == "preexisting"  # setdefault: process env wins


def test_env_file_command_substitution_resolves(tmp_path: Path, monkeypatch):
    """The regression behind OC's invalid-token flag: env files use $(...)."""
    monkeypatch.setattr(sessions, "_resolve_command", lambda c: None)
    monkeypatch.delenv("TOKEN_VIA_SUBST", raising=False)
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "export TOKEN_VIA_SUBST=$(echo resolved-secret)\n"
        "export DERIVED=$TOKEN_VIA_SUBST-suffix\n",
        encoding="utf-8",
    )
    (tmp_path / "prompt.txt").write_text("p", encoding="utf-8")
    cfg = PseudoOperatorConfig(
        loop_name="t",
        repo_root=tmp_path,
        session_prompt_file=Path("prompt.txt"),
        env_file=Path(".env.local"),
    )
    eng = PseudoOperatorEngine(cfg)
    env = eng._session_env("claude", {})
    assert env["TOKEN_VIA_SUBST"] == "resolved-secret"  # NOT the literal $(...)
    assert env["DERIVED"] == "resolved-secret-suffix"


def test_env_file_vars_literal_fallback(tmp_path: Path, monkeypatch):
    env_file = tmp_path / ".env.local"
    env_file.write_text("export A=$(echo x)\nB='lit'\n", encoding="utf-8")

    def _boom(*a, **k):
        raise OSError("no bash")

    monkeypatch.setattr(sessions.subprocess, "run", _boom)
    vars_ = sessions._env_file_vars(env_file)
    assert vars_["B"] == "lit"
    assert vars_["A"] == "$(echo x)"  # literal fallback, no expansion
