"""CL_ANCHOR resolution + validation.

Anchor inference (no MANIFEST arg) and name-based lookup ("PlatformManifest")
go through RepoGraph's per-machine manifest registry (ADR 0002 P2.4).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from context_lifecycle.errors import (
    AmbiguousAnchor,
    AnchorInvalid,
    AnchorMissing,
    AnchorPrerequisitesMissing,
    DirtyAnchor,
    ManifestNotFound,
)

ENV_VAR = "CL_ANCHOR"


def require_anchor_env() -> Path:
    """Return validated `CL_ANCHOR` path or raise AnchorMissing.

    Matches the locked spec (ADR 0002 P0.6): hard error, no fallback.
    """
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        raise AnchorMissing(
            "ContextLifecycle: no session anchor set (CL_ANCHOR is unset).\n"
            "Run `eval $(cl session start <manifest>)` before invoking Claude Code in this repo."
        )
    path = Path(raw)
    if not path.exists():
        raise AnchorInvalid(f"CL_ANCHOR points to non-existent path: {raw}")
    if not path.is_dir():
        raise AnchorInvalid(f"CL_ANCHOR is not a directory: {raw}")
    return path.resolve()


def resolve_anchor_arg(manifest: str | None) -> Path:
    """Resolve a manifest name or path argument to an absolute Path.

    - ``manifest=None`` → ask RepoGraph to infer from cwd (P2.4).
      ``AmbiguousAnchor`` on multiple matches; ``ManifestNotFound`` on none.
    - Path-like argument (contains ``/`` or is absolute) → resolve as filesystem path.
    - Bare name → look up in RepoGraph registry by manifest basename or
      canonical name.
    """
    if manifest is None:
        return _infer_anchor_via_repograph()

    looks_like_path = "/" in manifest or manifest.startswith(".") or manifest.startswith("~")
    if looks_like_path:
        p = Path(manifest).expanduser()
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        else:
            p = p.resolve()
        if not p.exists():
            raise ManifestNotFound(f"manifest path does not exist: {manifest}")
        if not p.is_dir():
            raise ManifestNotFound(f"manifest path is not a directory: {p}")
        return p

    # Bare name → registry lookup.
    return _resolve_name_via_repograph(manifest)


def _load_repograph():
    """Import RepoGraph lazily so tests can monkeypatch and so a missing dep
    surfaces as a clean error rather than an import-time crash."""
    try:
        from repograph import RepoGraph  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ManifestNotFound(
            "RepoGraph is not installed. Install it (`pip install repograph` or "
            "`pip install -e ../RepoGraph`) and register manifests via "
            "`repograph manifest add <path>`."
        ) from exc
    return RepoGraph


def _infer_anchor_via_repograph() -> Path:
    RepoGraph = _load_repograph()
    try:
        from repograph.errors import AmbiguousAnchorError  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - older RepoGraph
        AmbiguousAnchorError = Exception  # type: ignore[assignment]

    try:
        inferred = RepoGraph().find_anchor_for_path(Path.cwd())
    except AmbiguousAnchorError as exc:
        raise AmbiguousAnchor(str(exc)) from exc
    if inferred is None:
        raise ManifestNotFound(
            "could not infer anchor manifest from cwd. "
            "Pass an explicit `cl session start <manifest>` or register "
            "the owning manifest via `repograph manifest add <path>`."
        )
    return Path(inferred).resolve()


def _resolve_name_via_repograph(name: str) -> Path:
    RepoGraph = _load_repograph()
    view = RepoGraph().authorization()
    record = view.get_by_name(name)
    if record is None:
        raise ManifestNotFound(
            f"manifest {name!r} is not registered with RepoGraph. "
            f"Known: {sorted(r.name for r in view.manifests.values())}. "
            "Register it via `repograph manifest add <path>`."
        )
    return record.root


def validate_anchor(
    path: Path,
    *,
    require_clean: bool = False,
    require_context_skeleton: bool = False,
) -> None:
    """Validate an anchor path is usable.

    `require_context_skeleton`: if True, require `<path>/.context/` to exist.
    `require_clean`: if True, require the git working tree to be clean.

    Raises AnchorPrerequisitesMissing / DirtyAnchor on violation.
    """
    if require_context_skeleton:
        ctx = path / ".context"
        if not ctx.is_dir():
            raise AnchorPrerequisitesMissing(
                f"anchor manifest is missing .context/ skeleton: {path}"
            )
    if require_clean:
        if not _git_is_clean(path):
            raise DirtyAnchor(
                f"anchor manifest has uncommitted changes (--require-clean): {path}"
            )


def _git_is_clean(path: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        return False
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not a git repo / git missing: treat as not-clean to be safe.
        return False
    return result.stdout.strip() == ""
