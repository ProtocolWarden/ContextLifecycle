# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Committed-truth check (C2) — the keyless analogue of Track C's anchor.

No signed reference: the live section is compared against the committed copy on
origin/main. Match runs live; drift runs the COMMITTED copy (drift_unsigned) or
refuses under --require-committed; an unreachable origin degrades to unsigned
with a loud note (but --require-committed refuses — it cannot confirm truth).
A signed reference present ⇒ Track C wins, C2 is not consulted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from typer.testing import CliRunner

from context_lifecycle.cli import loop as loop_cli
from context_lifecycle.pseudo_operator import signing
from context_lifecycle.pseudo_operator.committed import CommittedResult, verify_committed
from context_lifecycle.pseudo_operator.config import load_verified_config
from context_lifecycle.pseudo_operator.signing import sign_reference

runner = CliRunner()

_CONFIG = (
    "pseudo_operator:\n"
    "  loop_name: t\n"
    "  session_prompt_file: prompt.txt\n"
    "  max_iterations: 7\n"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: Path) -> Path:
    """A git work-repo whose origin/main holds a committed loop config.

    Returns the path to the working config file. ``repo_root`` is injected by
    the loader (defaults to the config file's parent), so it is not committed.
    """
    work = tmp_path / "work"
    origin = tmp_path / "origin.git"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "t")
    (work / "prompt.txt").write_text("x", encoding="utf-8")
    cfg = work / "config.yaml"
    cfg.write_text(_CONFIG, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    origin.mkdir()
    _git(origin, "init", "--bare", "-b", "main")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    return cfg


def _drift(cfg: Path) -> None:
    """Loosen a guardrail in the working config without committing it."""
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace("max_iterations: 7", "max_iterations: 999999"),
        encoding="utf-8",
    )


# ── verify_committed ──────────────────────────────────────────────────────────
def test_match_when_live_equals_committed(tmp_path: Path):
    cfg = _repo(tmp_path)
    result = verify_committed(cfg)
    assert isinstance(result, CommittedResult)
    assert result.status == "match"
    assert result.committed is not None


def test_drift_detected_and_committed_section_returned(tmp_path: Path):
    cfg = _repo(tmp_path)
    _drift(cfg)
    result = verify_committed(cfg)
    assert result.status == "drift"
    assert result.committed["max_iterations"] == 7


def test_skip_when_origin_unreachable(tmp_path: Path):
    cfg = _repo(tmp_path)
    # Repoint origin at a path that does not exist → fetch fails → skip.
    _git(cfg.parent, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    result = verify_committed(cfg)
    assert result.status == "skip"
    assert "fetch failed" in result.detail


def test_skip_when_config_absent_on_origin(tmp_path: Path):
    cfg = _repo(tmp_path)
    other = cfg.parent / "not_committed.yaml"
    other.write_text(_CONFIG, encoding="utf-8")
    result = verify_committed(other)
    assert result.status == "skip"
    assert "absent on origin/main" in result.detail


# ── load_verified_config (C2 wiring) ─────────────────────────────────────────
def test_no_signed_plus_match_runs_live_unsigned(tmp_path: Path):
    cfg = _repo(tmp_path)
    loaded, status, _ = load_verified_config(cfg)
    assert status == "unsigned"
    assert loaded.max_iterations == 7


def test_no_signed_plus_drift_runs_committed_and_flags(tmp_path: Path):
    cfg = _repo(tmp_path)
    _drift(cfg)
    loaded, status, _ = load_verified_config(cfg)
    assert status == "drift_unsigned"
    assert loaded.max_iterations == 7  # the COMMITTED cap, not the tampered one


def test_no_signed_plus_drift_plus_require_committed_refuses(tmp_path: Path):
    cfg = _repo(tmp_path)
    _drift(cfg)
    with pytest.raises(ValueError, match="require-committed"):
        load_verified_config(cfg, require_committed=True)


def test_offline_skips_check_and_runs_live_loud(tmp_path: Path):
    cfg = _repo(tmp_path)
    _drift(cfg)  # would be drift, but origin is unreachable → skip, run live
    _git(cfg.parent, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    loaded, status, detail = load_verified_config(cfg)
    assert status == "unsigned"
    assert loaded.max_iterations == 999999  # ran the live section, check skipped
    assert "committed-truth check skipped" in detail


def test_require_committed_refuses_when_offline(tmp_path: Path):
    cfg = _repo(tmp_path)
    _drift(cfg)
    _git(cfg.parent, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    # Fail-closed gate: an unreachable origin means the gate cannot confirm the
    # live section matches committed truth, so --require-committed must refuse
    # (parity with --require-signed). A fail-open skip here would let anyone
    # bypass the gate by cutting network access.
    with pytest.raises(ValueError, match="could not be verified against origin/main"):
        load_verified_config(cfg, require_committed=True)


def test_signed_reference_present_bypasses_c2(tmp_path: Path):
    cfg = _repo(tmp_path)
    key = Ed25519PrivateKey.generate()
    priv = cfg.parent / "operator_priv.pem"
    priv.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    (cfg.parent / signing.PUBKEY_NAME).write_text(
        key.public_key().public_bytes_raw().hex() + "\n", encoding="utf-8"
    )
    sign_reference(cfg, priv)
    _drift(cfg)
    # Track C wins: status is signed "drift" (not the keyless "drift_unsigned"),
    # even though the working tree also diverges from origin/main.
    loaded, status, _ = load_verified_config(cfg, require_committed=True)
    assert status == "drift"
    assert loaded.max_iterations == 7


# ── CLI surface ───────────────────────────────────────────────────────────────
def test_cli_run_drift_unsigned_marks_state(tmp_path: Path, monkeypatch):
    cfg = _repo(tmp_path)
    _drift(cfg)

    captured: dict = {}

    class FakeEngine:
        def __init__(self, c):
            captured["cfg"] = c
            self.signed_status = "unsigned"

        def run(self):
            captured["signed_status"] = self.signed_status
            return 0

    monkeypatch.setattr(loop_cli, "PseudoOperatorEngine", FakeEngine)
    res = runner.invoke(loop_cli.app, ["run", "--config", str(cfg)])
    assert res.exit_code == 0
    assert "DIVERGES from origin/main" in res.output
    assert captured["cfg"].max_iterations == 7
    assert captured["signed_status"] == "drift_unsigned"


def test_cli_run_require_committed_refuses_on_drift(tmp_path: Path):
    cfg = _repo(tmp_path)
    _drift(cfg)
    res = runner.invoke(
        loop_cli.app, ["run", "--config", str(cfg), "--require-committed"]
    )
    assert res.exit_code == 2
    assert "require-committed" in res.output
