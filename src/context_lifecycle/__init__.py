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

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "CLError",
    "AnchorMissing",
    "AnchorInvalid",
    "AmbiguousAnchor",
    "BoundaryViolation",
    "ManifestNotFound",
    "SessionNotStarted",
    "hydrate",
    "capture",
    "peek",
]


def hydrate(lineage_id: str, work_item: object) -> None:  # pragma: no cover - P4 stub
    """Hydrate worker context before dispatch. Implemented in P4."""
    raise NotImplementedError("hydrate is implemented in Phase 4")


def capture(lineage_id: str, result: object) -> None:  # pragma: no cover - P4 stub
    """Capture worker result after dispatch. Implemented in P4."""
    raise NotImplementedError("capture is implemented in Phase 4")


def peek(work_item: object) -> None:  # pragma: no cover - P4 stub
    """Read-only context inspection. Implemented in P4."""
    raise NotImplementedError("peek is implemented in Phase 4")
