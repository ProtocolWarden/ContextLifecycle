# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""ContextLifecycle — cognition lifecycle schemas, I/O, policy enforcement."""

from context_lifecycle.errors import (
    CLError,
    AnchorMissing,
    AnchorInvalid,
    AmbiguousAnchor,
    BoundaryViolation,
    ManifestNotFound,
    SessionNotStarted,
)
from context_lifecycle.lifecycle import (
    HydratedContext,
    capture,
    hydrate,
    peek,
)

__version__ = "0.3.0"

__all__ = [
    "__version__",
    "CLError",
    "AnchorMissing",
    "AnchorInvalid",
    "AmbiguousAnchor",
    "BoundaryViolation",
    "ManifestNotFound",
    "SessionNotStarted",
    "HydratedContext",
    "hydrate",
    "capture",
    "peek",
]
