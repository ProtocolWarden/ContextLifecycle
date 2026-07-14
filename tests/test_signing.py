# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Signed loop config — the live-plane trust anchor (Track C).

Deploy-only-from-signed-reference: drift runs the reference, a bad signature
refuses, unsigned is loud (and refusable via --require-signed).
"""

from __future__ import annotations

import json
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
from context_lifecycle.pseudo_operator.config import load_verified_config
from context_lifecycle.cli.loop import sign_config_cmd, verify_config_cmd
from context_lifecycle.pseudo_operator.signing import (
    VerifyResult,
    canonical_bytes,
    sign_reference,
    verify_reference,
)

assert all((sign_config_cmd, verify_config_cmd))  # T1 references


def test_canonical_bytes_is_deterministic():
    a = canonical_bytes({"b": 1, "a": {"y": 2, "x": 3}})
    b = canonical_bytes({"a": {"x": 3, "y": 2}, "b": 1})
    assert a == b


def test_verify_result_signed_ok_property():
    assert VerifyResult("ok", "", {}).signed_ok
    assert VerifyResult("drift", "", {}).signed_ok
    assert not VerifyResult("unsigned", "", None).signed_ok
    assert not VerifyResult("bad_signature", "", None).signed_ok

runner = CliRunner()


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    """A repo with a loop config + an operator keypair; returns (config, privkey)."""
    (tmp_path / "prompt.txt").write_text("x", encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "pseudo_operator:\n"
        "  loop_name: t\n"
        f"  repo_root: {tmp_path}\n"
        "  session_prompt_file: prompt.txt\n"
        "  max_iterations: 7\n",
        encoding="utf-8",
    )
    key = Ed25519PrivateKey.generate()
    priv = tmp_path / "operator_priv.pem"
    priv.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    (tmp_path / signing.PUBKEY_NAME).write_text(
        key.public_key().public_bytes_raw().hex() + "\n", encoding="utf-8"
    )
    return cfg, priv


def test_section_extractors_resolve_same_scope(tmp_path: Path):
    """load_section (from file) and section_of (from parsed data) must resolve
    the SAME anchored section — the single source of truth the signed (Track C)
    and committed (C2) checks both compare, so their scopes can never diverge."""
    cfg, _ = _repo(tmp_path)
    import yaml

    from_file = signing.load_section(cfg)
    from_data = signing.section_of(yaml.safe_load(cfg.read_text(encoding="utf-8")))
    assert from_file == from_data
    assert from_file["max_iterations"] == 7
    # A mapping without a pseudo_operator key falls back to the whole mapping;
    # a non-mapping raises (the never-anchor-junk guard).
    assert signing.section_of({"loop_name": "x"}) == {"loop_name": "x"}
    with pytest.raises(ValueError, match="not a YAML mapping"):
        signing.section_of(["not", "a", "mapping"])


def test_unsigned_when_no_reference(tmp_path: Path):
    cfg, _ = _repo(tmp_path)
    assert verify_reference(cfg).status == "unsigned"
    loaded, status, _ = load_verified_config(cfg)
    assert status == "unsigned" and loaded.max_iterations == 7


def test_sign_then_ok(tmp_path: Path):
    cfg, priv = _repo(tmp_path)
    ref = sign_reference(cfg, priv)
    assert ref.exists()
    result = verify_reference(cfg)
    assert result.status == "ok"
    _, status, _ = load_verified_config(cfg)
    assert status == "ok"


def test_drift_runs_the_reference_not_the_live_section(tmp_path: Path):
    cfg, priv = _repo(tmp_path)
    sign_reference(cfg, priv)
    # Attacker/agent loosens a guardrail in the live config.
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_iterations: 7", "max_iterations: 999999"), encoding="utf-8")
    result = verify_reference(cfg)
    assert result.status == "drift"
    loaded, status, _ = load_verified_config(cfg)
    assert status == "drift"
    assert loaded.max_iterations == 7  # the SIGNED cap, not the tampered one


def test_tampered_reference_refuses(tmp_path: Path):
    cfg, priv = _repo(tmp_path)
    ref = sign_reference(cfg, priv)
    # Attacker edits the reference itself (cannot re-sign without the key).
    section = json.loads(ref.read_text(encoding="utf-8"))
    section["max_iterations"] = 999999
    ref.write_text(json.dumps(section, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    assert verify_reference(cfg).status == "bad_signature"
    with pytest.raises(ValueError, match="FAILED verification"):
        load_verified_config(cfg)


def test_wrong_key_signature_refuses(tmp_path: Path):
    cfg, priv = _repo(tmp_path)
    sign_reference(cfg, priv)
    # Attacker generates their OWN key and re-signs — pinned pubkey wins.
    attacker = Ed25519PrivateKey.generate()
    ref = tmp_path / signing.REFERENCE_NAME
    (tmp_path / signing.SIGNATURE_NAME).write_text(
        attacker.sign(ref.read_bytes()).hex() + "\n", encoding="utf-8"
    )
    assert verify_reference(cfg).status == "bad_signature"


def test_require_signed_refuses_unsigned(tmp_path: Path):
    cfg, _ = _repo(tmp_path)
    with pytest.raises(ValueError, match="not anchored"):
        load_verified_config(cfg, require_signed=True)


def test_cli_verify_config_exit_codes(tmp_path: Path):
    cfg, priv = _repo(tmp_path)
    res = runner.invoke(loop_cli.app, ["verify-config", "--config", str(cfg)])
    assert res.exit_code == 5  # unsigned
    res = runner.invoke(
        loop_cli.app, ["sign-config", "--config", str(cfg), "--key", str(priv)]
    )
    assert res.exit_code == 0
    res = runner.invoke(loop_cli.app, ["verify-config", "--config", str(cfg)])
    assert res.exit_code == 0
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_iterations: 7", "max_iterations: 9"), encoding="utf-8")
    res = runner.invoke(loop_cli.app, ["verify-config", "--config", str(cfg)])
    assert res.exit_code == 3  # drift


def test_cli_run_require_signed_refuses(tmp_path: Path, monkeypatch):
    cfg, _ = _repo(tmp_path)
    res = runner.invoke(
        loop_cli.app, ["run", "--config", str(cfg), "--require-signed"]
    )
    assert res.exit_code == 2
    assert "not anchored" in res.output


def test_cli_run_drift_uses_reference_and_marks_state(tmp_path: Path, monkeypatch):
    cfg, priv = _repo(tmp_path)
    sign_reference(cfg, priv)
    cfg.write_text(cfg.read_text(encoding="utf-8").replace("max_iterations: 7", "max_iterations: 999999"), encoding="utf-8")

    captured = {}

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
    assert "DIVERGES" in res.output
    assert captured["cfg"].max_iterations == 7
    assert captured["signed_status"] == "drift"
