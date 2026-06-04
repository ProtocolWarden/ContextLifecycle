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
    """Find a registered private-manifest repo root via RepoGraph, if installed.

    Prefers RepoGraph's shared role resolver
    (``repograph.resolve_private_manifest``, the canonical promotion of this
    logic — see PlatformManifest's private-manifest-role-generalization
    design). Falls back to the same discovery inline for older RepoGraph
    installs that predate the API: ``Registry.load().manifests`` is the
    list of registered manifest-repo roots, and ``discover_all_manifest_yamls``
    returns every manifest YAML a root hosts (a private repo may host nested
    per-project manifests, e.g. ``manifests/<project>/private_manifest.yaml``).
    A root is *private* when any of its manifest YAMLs is named
    ``private_manifest*`` — matched on the YAML basename, never a hardcoded repo
    name (I2).
    """
    try:
        from repograph import resolve_private_manifest  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        try:
            return resolve_private_manifest()
        except Exception:  # noqa: BLE001 - discovery is best-effort
            return None
    try:
        from repograph import Registry  # type: ignore[import-not-found]
        from repograph.registry import (  # type: ignore[import-not-found]
            discover_all_manifest_yamls,
        )
    except ImportError:
        return None
    try:
        registry = Registry.load()
    except Exception:  # noqa: BLE001 - discovery is best-effort
        return None
    for root in registry.manifests:
        root = Path(root).expanduser()
        if not root.is_dir():
            continue
        try:
            yamls = discover_all_manifest_yamls(root)
        except Exception:  # noqa: BLE001 - per-root discovery is best-effort
            continue
        if any(y.name.startswith("private_manifest") for y in yamls):
            return root.resolve()
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
