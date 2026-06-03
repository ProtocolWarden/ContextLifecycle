# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Typed errors for ContextLifecycle.

Error → exit-code mapping for CLI surfaces is owned by the CLI layer, not
the error classes themselves. See `cli/session.py` for the mapping per the
locked spec in ADR 0002 P0.2.
"""

from __future__ import annotations


class CLError(Exception):
    """Base class for ContextLifecycle errors."""


class AnchorMissing(CLError):
    """`CL_ANCHOR` env var is not set when a hook fires."""


class AnchorInvalid(CLError):
    """`CL_ANCHOR` path does not exist or is not a manifest repo."""


class AmbiguousAnchor(CLError):
    """RepoGraph found multiple candidate anchors for the given cwd."""


class ManifestNotFound(CLError):
    """Manifest argument did not resolve to a real path."""


class SessionNotStarted(CLError):
    """No active session (CL_ANCHOR / CL_SESSION_ID unset)."""


class BoundaryViolation(CLError):
    """A write would violate the active anchor's authorization scope."""


class DirtyAnchor(CLError):
    """Anchor manifest has uncommitted changes and --require-clean is set."""


class AnchorPrerequisitesMissing(CLError):
    """Anchor manifest is missing the .context/ skeleton or similar."""
