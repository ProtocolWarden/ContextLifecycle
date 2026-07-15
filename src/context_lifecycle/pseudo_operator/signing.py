# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Signed loop config — the live-plane trust anchor (Track C).

Deploy-only-from-signed-reference (control_plane_and_anchor.md): the operator
signs the ``pseudo_operator:`` section once (ed25519, same key conventions as
the OC EVAL corpus — PEM private key kept OFF-INFRA, pubkey hex committed and
CODEOWNERS-pinned); after that the engine CONSUMES the signed reference, so
"restore vs. change" is decidable without an intent-classifier:

- drift (live section != signed reference) → the engine runs the REFERENCE
  and flags the divergence — restore-by-consumption, no YAML rewriting, no
  write-path for the fleet into its own guardrails;
- change → the operator signs a new reference (`cl loop sign-config`), the
  only legitimate write path.

File conventions, all beside the repo config file:
  operator_pubkey.ed25519        — pubkey hex, sole first line (committed,
                                   CODEOWNERS-pinned; OC may reuse the EVAL key)
  pseudo_operator.signed.json    — canonical JSON snapshot of the signed section
  pseudo_operator.signed.sig     — ed25519 signature hex over the snapshot bytes

Threat notes: a local agent that deletes the reference/pubkey downgrades the
loop to unsigned mode — that is LOUD (warning + runtime state) and the
committed truth is restored by git; launchers pass ``--require-signed`` once
anchored so the downgrade refuses to run instead. The private key never
touches a fleet host; signature verification is the no-self-rewrite invariant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import load_pem_private_key

REFERENCE_NAME = "pseudo_operator.signed.json"
SIGNATURE_NAME = "pseudo_operator.signed.sig"
PUBKEY_NAME = "operator_pubkey.ed25519"


def canonical_bytes(section: dict) -> bytes:
    """Deterministic byte form of a config section (sorted keys, no ASCII escapes)."""
    return json.dumps(section, sort_keys=True, ensure_ascii=False).encode("utf-8")


def _paths(config_file: Path) -> tuple[Path, Path, Path]:
    d = config_file.resolve().parent
    return d / REFERENCE_NAME, d / SIGNATURE_NAME, d / PUBKEY_NAME


def section_of(data: object, *, source: str = "config") -> dict:
    """Extract the anchored ``pseudo_operator`` section from parsed YAML data.

    The single source of truth for what a signed/committed anchor covers, so the
    file path (:func:`load_section`) and any already-parsed source (e.g. the
    committed bytes from ``git show``) resolve the SAME scope.
    """
    if not isinstance(data, dict):
        raise ValueError(f"{source}: not a YAML mapping")
    section = data.get("pseudo_operator", data)
    if not isinstance(section, dict):
        raise ValueError(f"{source}: no pseudo_operator section")
    return section


def load_section(config_file: Path) -> dict:
    """Load and extract the anchored section from a YAML config file."""
    from context_lifecycle.io.yaml_io import load_yaml_safe

    return section_of(load_yaml_safe(config_file, default=None), source=str(config_file))


def sign_reference(config_file: Path, private_key_path: Path) -> Path:
    """Operator tool: snapshot + sign the live ``pseudo_operator:`` section.

    Run OFFLINE with the operator's private key (PEM, as produced by the EVAL
    keygen). Writes the canonical snapshot + signature beside the config file;
    commit both. Never called by any fleet path.
    """
    section = load_section(config_file)
    payload = canonical_bytes(section)
    data = private_key_path.read_bytes()
    key = load_pem_private_key(data, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("signing key must be an Ed25519 private key (PEM)")
    ref_path, sig_path, _ = _paths(config_file)
    ref_path.write_bytes(payload)
    sig_path.write_text(key.sign(payload).hex() + "\n", encoding="utf-8")
    return ref_path


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of verifying the live section against the signed reference."""

    status: str  # "ok" | "drift" | "bad_signature" | "unsigned"
    detail: str
    reference: dict | None  # the verified signed section (ok/drift), else None

    @property
    def signed_ok(self) -> bool:
        return self.status in {"ok", "drift"}


def verify_reference(config_file: Path) -> VerifyResult:
    """Verify signature + live-section match. Never raises on trust failures.

    - ``ok``: signature valid, live section byte-identical to the reference.
    - ``drift``: signature valid, live section DIVERGES — the caller must run
      the reference, not the live section (restore-by-consumption).
    - ``bad_signature``: reference present but the signature does not verify —
      the reference itself is untrustworthy; refuse to run signed.
    - ``unsigned``: no reference and/or pubkey — the repo is not anchored yet.
    """
    ref_path, sig_path, pub_path = _paths(config_file)
    if not (ref_path.exists() and sig_path.exists() and pub_path.exists()):
        missing = [p.name for p in (ref_path, sig_path, pub_path) if not p.exists()]
        return VerifyResult("unsigned", f"missing: {', '.join(missing)}", None)
    try:
        pub_hex = pub_path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
        pubkey = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
    except (OSError, ValueError, IndexError) as exc:
        return VerifyResult("bad_signature", f"unusable pubkey: {exc}", None)
    payload = ref_path.read_bytes()
    try:
        sig = bytes.fromhex(sig_path.read_text(encoding="utf-8").strip())
        pubkey.verify(sig, payload)
    except (OSError, ValueError, InvalidSignature) as exc:
        return VerifyResult("bad_signature", f"signature does not verify: {exc}", None)
    try:
        reference = json.loads(payload)
    except ValueError as exc:
        return VerifyResult("bad_signature", f"reference is not valid JSON: {exc}", None)
    try:
        live = load_section(config_file)
    except ValueError as exc:
        return VerifyResult("drift", f"live config unreadable: {exc}", reference)
    if canonical_bytes(live) != canonical_bytes(reference):
        return VerifyResult(
            "drift",
            "live pseudo_operator section diverges from the signed reference",
            reference,
        )
    return VerifyResult("ok", "signature valid; live section matches", reference)


__all__ = [
    "PUBKEY_NAME",
    "REFERENCE_NAME",
    "SIGNATURE_NAME",
    "VerifyResult",
    "canonical_bytes",
    "load_section",
    "section_of",
    "sign_reference",
    "verify_reference",
]
