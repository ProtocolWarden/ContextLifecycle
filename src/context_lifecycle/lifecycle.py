# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Public Python API for dispatcher integration (ADR 0002 P4).

Three functions OC's dispatcher (and any future dispatcher) wraps around
each executor invocation:

- ``hydrate(lineage_id, work_item)`` — before dispatch. Loads any existing
  active capsule for the lineage from the anchor manifest's
  ``.context/sessions/<sid>/active/<lineage_id>.yaml``, or initializes a
  fresh ``HydratedContext`` if none exists.
- ``capture(lineage_id, result)`` — after dispatch. Writes the result to
  the appropriate session subdir (``active/`` for capsule updates,
  ``checkpoints/`` for checkpoints, ``handoffs/`` for handoffs). Every
  write is gated by ``RepoGraph.can_anchor_host()`` for each repo named
  in the result; a single denial aborts the whole write with
  ``BoundaryViolation``.
- ``peek(work_item)`` — read-only. Returns the raw active capsule dict
  for the work_item's lineage, or ``None`` when no active state exists.
  Never writes, never falls back to checkpoint/handoff.

All three hard-error with ``AnchorMissing`` / ``SessionNotStarted`` when
``CL_ANCHOR`` / ``CL_SESSION_ID`` are unset, per ADR 0002 P0.6.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from context_lifecycle.errors import BoundaryViolation
from context_lifecycle.io.yaml_io import dump_yaml, load_yaml_safe
from context_lifecycle.session.anchor import require_anchor_env
from context_lifecycle.session.ids import require_session_env
from context_lifecycle.session.paths import SessionPaths


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------


@dataclass
class HydratedContext:
    """Structured context returned by :func:`hydrate`.

    ``capsule`` is always a dict — either the existing on-disk capsule
    or a freshly initialized one with ``lineage_id`` / ``session_id`` /
    ``status="fresh"`` / ``created_at`` populated.
    """

    lineage_id: str
    session_id: str
    anchor_path: Path
    capsule: dict[str, Any] = field(default_factory=dict)
    latest_checkpoint: dict[str, Any] | None = None
    active_handoff: dict[str, Any] | None = None
    fresh: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "lineage_id": self.lineage_id,
            "session_id": self.session_id,
            "anchor_path": str(self.anchor_path),
            "capsule": dict(self.capsule),
            "latest_checkpoint": (
                dict(self.latest_checkpoint) if self.latest_checkpoint else None
            ),
            "active_handoff": (
                dict(self.active_handoff) if self.active_handoff else None
            ),
            "fresh": self.fresh,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _session_paths() -> SessionPaths:
    """Resolve CL_ANCHOR + CL_SESSION_ID into a SessionPaths handle."""
    anchor = require_anchor_env()
    sid = require_session_env()
    return SessionPaths(anchor=anchor, session_id=sid)


def _active_file(paths: SessionPaths, lineage_id: str) -> Path:
    return paths.active / f"{lineage_id}.yaml"


def _latest_checkpoint(paths: SessionPaths) -> dict[str, Any] | None:
    """Return the lexicographically last `.yaml` under ``checkpoints/``.

    Checkpoint filenames are ISO-8601 UTC (P0.5), so lexicographic sort
    coincides with chronological order.
    """
    cp_dir = paths.checkpoints
    if not cp_dir.is_dir():
        return None
    candidates = sorted(p for p in cp_dir.glob("*.yaml") if p.is_file())
    if not candidates:
        return None
    data = load_yaml_safe(candidates[-1], default=None)
    return data if isinstance(data, dict) else None


def _active_handoff(paths: SessionPaths, lineage_id: str) -> dict[str, Any] | None:
    """Return the handoff yaml matching ``lineage_id`` if one exists.

    Handoff filenames are worker-named (e.g. ``worker-1.yaml``); we match
    by reading each and looking for ``lineage_id`` inside. Cheap because
    handoff counts per session are small.
    """
    ho_dir = paths.handoffs
    if not ho_dir.is_dir():
        return None
    for path in sorted(ho_dir.glob("*.yaml")):
        data = load_yaml_safe(path, default=None)
        if isinstance(data, dict) and data.get("lineage_id") == lineage_id:
            return data
    return None


def _extract_repos(result: dict[str, Any]) -> list[str]:
    """Pull every repo name referenced in ``result``.

    Looked-at keys (any may be missing):
      - ``repo`` / ``repo_name`` / ``repo_key`` — top-level scalar
      - ``repos`` / ``repos_touched`` / ``targets`` / ``target_repos`` — list of repo names
      - ``worker_scope.repo`` — handoff shape
    """
    repos: list[str] = []
    for key in ("repo", "repo_name", "repo_key"):
        val = result.get(key)
        if isinstance(val, str) and val:
            repos.append(val)
    for key in ("repos", "repos_touched", "targets", "target_repos"):
        val = result.get(key)
        if isinstance(val, list):
            repos.extend(str(v) for v in val if isinstance(v, str))
    scope = result.get("worker_scope")
    if isinstance(scope, dict):
        scope_repo = scope.get("repo")
        if isinstance(scope_repo, str) and scope_repo:
            repos.append(scope_repo)
    # Deduplicate, preserve first-seen order.
    seen: set[str] = set()
    unique: list[str] = []
    for r in repos:
        if r not in seen:
            seen.add(r)
            unique.append(r)
    return unique


def _classify(result: dict[str, Any]) -> str:
    """Choose target subdir based on result shape.

    Order matters — handoff is checked first (most specific), then
    checkpoint, then capsule (default).
    """
    explicit = result.get("kind")
    if explicit in {"capsule", "checkpoint", "handoff"}:
        return explicit  # type: ignore[return-value]
    if "handoff_id" in result or "worker_scope" in result:
        return "handoff"
    if "checkpoint_id" in result or "orchestrator" in result:
        return "checkpoint"
    return "capsule"


def _enforce_boundary(anchor: Path, repos: Iterable[str]) -> None:
    """Call RepoGraph.can_anchor_host for each repo; raise on any denial.

    RepoGraph is imported lazily so a missing install surfaces as a
    BoundaryViolation rather than an import error at module load.
    """
    repo_list = [r for r in repos if r]
    if not repo_list:
        return  # nothing to authorize
    try:
        from repograph import RepoGraph  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - dev env always has it
        raise BoundaryViolation(
            "RepoGraph is not installed; cannot authorize capture write. "
            "Install RepoGraph (`pip install -e ../RepoGraph`)."
        ) from exc
    graph = RepoGraph()
    for repo in repo_list:
        allowed, reason = graph.can_anchor_host(anchor, repo)
        if not allowed:
            raise BoundaryViolation(
                f"capture blocked for repo {repo!r} under anchor {anchor}: {reason}"
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def hydrate(lineage_id: str, work_item: dict[str, Any]) -> HydratedContext:
    """Load (or initialize) the context capsule for ``lineage_id``.

    Reads ``<CL_ANCHOR>/.context/sessions/<CL_SESSION_ID>/active/<lineage_id>.yaml``
    if present (resume). Otherwise initializes a fresh capsule and returns
    it without writing to disk — :func:`capture` is the only writer.

    ``work_item`` is recorded on the fresh capsule as ``work_item`` for
    downstream observability but is otherwise opaque to CL.
    """
    paths = _session_paths()
    active = _active_file(paths, lineage_id)
    existing = load_yaml_safe(active, default=None)
    if isinstance(existing, dict):
        capsule = existing
        fresh = False
    else:
        capsule = {
            "lineage_id": lineage_id,
            "session_id": paths.session_id,
            "status": "fresh",
            "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "work_item": dict(work_item) if isinstance(work_item, dict) else work_item,
        }
        fresh = True
    return HydratedContext(
        lineage_id=lineage_id,
        session_id=paths.session_id,
        anchor_path=paths.anchor,
        capsule=capsule,
        latest_checkpoint=_latest_checkpoint(paths),
        active_handoff=_active_handoff(paths, lineage_id),
        fresh=fresh,
    )


def capture(lineage_id: str, result: dict[str, Any]) -> None:
    """Persist ``result`` under the active session.

    Subdir is chosen by :func:`_classify`. RepoGraph authorization is
    enforced for every repo named in ``result``; on the first denial the
    function raises ``BoundaryViolation`` and writes nothing.
    """
    if not isinstance(result, dict):
        raise TypeError(f"capture(result=...) must be a dict, got {type(result).__name__}")
    paths = _session_paths()
    paths.ensure()

    _enforce_boundary(paths.anchor, _extract_repos(result))

    kind = _classify(result)
    if kind == "capsule":
        target = _active_file(paths, lineage_id)
    elif kind == "checkpoint":
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
        ckpt_id = result.get("checkpoint_id") or ts
        target = paths.checkpoints / f"{ckpt_id}.yaml"
    else:  # handoff
        worker = (
            result.get("handoff_id")
            or result.get("worker_id")
            or lineage_id
        )
        target = paths.handoffs / f"{worker}.yaml"

    payload = dict(result)
    payload.setdefault("lineage_id", lineage_id)
    payload.setdefault("session_id", paths.session_id)
    payload.setdefault(
        "captured_at",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    dump_yaml(target, payload)


def peek(work_item: dict[str, Any]) -> dict[str, Any] | None:
    """Read-only inspection of active state for ``work_item``'s lineage.

    Returns the active capsule dict if one exists, ``None`` otherwise.
    Never writes, never reads checkpoints or handoffs. Per ADR 0002 P4.3:
    available for router consumption; OC's dispatcher does not use this
    for routing decisions in P4.
    """
    lineage_id = None
    if isinstance(work_item, dict):
        for key in ("lineage_id", "lineage", "id"):
            val = work_item.get(key)
            if isinstance(val, str) and val:
                lineage_id = val
                break
    if lineage_id is None:
        return None
    paths = _session_paths()
    data = load_yaml_safe(_active_file(paths, lineage_id), default=None)
    return data if isinstance(data, dict) else None
