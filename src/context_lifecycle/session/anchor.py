"""CL_ANCHOR resolution + validation.

Anchor inference (no MANIFEST arg) requires RepoGraph and lands in P2; the
P1 CLI hard-errors when the positional arg is omitted.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from context_lifecycle.errors import (
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

    For P1 we only support absolute or relative paths. Name-based lookup
    (e.g. "PlatformManifest") is RepoGraph's job and lands in P2.
    """
    if manifest is None:
        raise ManifestNotFound(
            "anchor inference not yet implemented (Phase 2). "
            "Pass an explicit manifest path: `cl session start <path>`."
        )
    p = Path(manifest).expanduser()
    if not p.is_absolute():
        # Try cwd-relative resolution.
        p = (Path.cwd() / p).resolve()
    else:
        p = p.resolve()
    if not p.exists():
        # Name-based lookup is a P2 feature; for now surface a clear error.
        if "/" not in manifest:
            raise ManifestNotFound(
                f"manifest '{manifest}' not found. Name-based lookup is a Phase 2 feature "
                "(needs RepoGraph manifest registry). Pass an absolute path for now."
            )
        raise ManifestNotFound(f"manifest path does not exist: {manifest}")
    if not p.is_dir():
        raise ManifestNotFound(f"manifest path is not a directory: {p}")
    return p


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
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not a git repo / git missing: treat as not-clean to be safe.
        return False
    return result.stdout.strip() == ""
