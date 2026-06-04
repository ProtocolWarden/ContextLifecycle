# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Resolve the private-archive destination (spec §3.3 / Layer C).

Pruned history lands under ``<private-manifest>/archive/console/<repo>/``. The
private-manifest repo name is **not** hardcoded in this source (boundary rule I2
/ [[private-manifest-generalization]]); it is resolved at runtime via:

1. ``$PRIVATE_MANIFEST_DIR`` — explicit operator-supplied root, else
2. RepoGraph registry discovery — the first registered manifest repo whose
   discovered YAML is a *private* manifest (``private_manifest.yaml`` shape).
"""

from __future__ import annotations

import os
from pathlib import Path

_PRIVATE_MANIFEST_ENV = "PRIVATE_MANIFEST_DIR"
_ARCHIVE_SUBPATH = Path("archive") / "console"


class PrivateArchiveUnavailable(RuntimeError):
    """Raised when no private-manifest destination can be resolved."""


def _discover_via_repograph() -> Path | None:
    """Find a registered private-manifest repo root via RepoGraph, if installed."""
    try:
        from repograph import Registry, discover_manifest_yaml  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        registry = Registry.load()
    except Exception:  # noqa: BLE001 - discovery is best-effort
        return None
    for root in registry.resolved_paths():
        manifest = discover_manifest_yaml(root)
        if manifest is None:
            continue
        # A private manifest is recognised by its discovered YAML basename — we
        # never match on a hardcoded repo name (I2).
        if manifest.name.startswith("private_manifest"):
            return root
    return None


def resolve_private_root() -> Path:
    """Return the private-manifest repo root, or raise PrivateArchiveUnavailable."""
    env = os.environ.get(_PRIVATE_MANIFEST_ENV, "").strip()
    if env:
        root = Path(env).expanduser()
        if root.is_dir():
            return root
        raise PrivateArchiveUnavailable(
            f"${_PRIVATE_MANIFEST_ENV} is set to {root} but it is not a directory"
        )
    discovered = _discover_via_repograph()
    if discovered is not None:
        return discovered
    raise PrivateArchiveUnavailable(
        f"no private-manifest root found: set ${_PRIVATE_MANIFEST_ENV} or register "
        "a private manifest with RepoGraph"
    )


def archive_dir_for(repo: str, *, private_root: Path | None = None) -> Path:
    """Return ``<private-root>/archive/console/<repo>/`` (not created here)."""
    root = private_root if private_root is not None else resolve_private_root()
    return root / _ARCHIVE_SUBPATH / repo
