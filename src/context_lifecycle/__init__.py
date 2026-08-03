# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""ContextLifecycle — cognition lifecycle schemas, I/O, policy enforcement."""

from context_lifecycle.errors import (
    AmbiguousAnchor,
    AnchorInvalid,
    AnchorMissing,
    BoundaryViolation,
    CLError,
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
    "AmbiguousAnchor",
    "AnchorInvalid",
    "AnchorMissing",
    "BoundaryViolation",
    "CLError",
    "HydratedContext",
    "ManifestNotFound",
    "SessionNotStarted",
    "__version__",
    "capture",
    "hydrate",
    "peek",
]
