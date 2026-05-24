"""`cl session` subcommands.

Exit codes (per ADR 0002 P0.2):
  0  anchor set successfully
  1  manifest not found / unresolvable
  2  ambiguous inference (no MANIFEST arg, RepoGraph found multiple)
  3  anchor manifest missing prerequisites (no .context/ skeleton, etc.)
  4  --require-clean violation
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer

from context_lifecycle.errors import (
    AmbiguousAnchor,
    AnchorInvalid,
    AnchorPrerequisitesMissing,
    DirtyAnchor,
    ManifestNotFound,
    SessionNotStarted,
)
from context_lifecycle.session.anchor import (
    ENV_VAR as ANCHOR_ENV,
    resolve_anchor_arg,
    validate_anchor,
)
from context_lifecycle.session.ids import (
    ENV_VAR as SESSION_ENV,
    generate_session_id,
    is_valid_session_id,
)
from context_lifecycle.session.paths import SessionPaths, archived_root

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("start")
def start(
    manifest: Optional[str] = typer.Argument(
        None,
        help="Manifest name or absolute path. If omitted, P2 will infer via RepoGraph.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of shell export lines."),
    shell: bool = typer.Option(False, "--shell", help="Spawn a subshell with env set."),
    require_clean: bool = typer.Option(
        False, "--require-clean", help="Fail if anchor manifest has uncommitted changes."
    ),
) -> None:
    """Start a session anchored at MANIFEST. Default output: shell `export` lines."""
    if json_out and shell:
        typer.echo("--json and --shell are mutually exclusive", err=True)
        raise typer.Exit(code=1)

    try:
        anchor = resolve_anchor_arg(manifest)
    except ManifestNotFound as e:
        typer.echo(f"cl session start: {e}", err=True)
        raise typer.Exit(code=1)
    except AmbiguousAnchor as e:  # pragma: no cover - P2
        typer.echo(f"cl session start: {e}", err=True)
        raise typer.Exit(code=2)

    try:
        validate_anchor(anchor, require_clean=require_clean, require_context_skeleton=False)
    except AnchorPrerequisitesMissing as e:
        typer.echo(f"cl session start: {e}", err=True)
        raise typer.Exit(code=3)
    except DirtyAnchor as e:
        typer.echo(f"cl session start: {e}", err=True)
        raise typer.Exit(code=4)

    session_id = generate_session_id()
    paths = SessionPaths(anchor=anchor, session_id=session_id)
    # Best-effort skeleton creation: hooks need these dirs to exist for find().
    try:
        paths.ensure()
    except OSError:
        # If we can't write here, the hook will surface the issue cleanly later.
        pass

    manifest_name = anchor.name

    if shell:
        env = os.environ.copy()
        env[ANCHOR_ENV] = str(anchor)
        env[SESSION_ENV] = session_id
        shell_bin = os.environ.get("SHELL", "/bin/bash")
        typer.echo(
            f"cl: spawning subshell with {ANCHOR_ENV}={anchor} {SESSION_ENV}={session_id}",
            err=True,
        )
        os.execvpe(shell_bin, [shell_bin], env)  # noqa: S606

    if json_out:
        typer.echo(
            json.dumps(
                {
                    "anchor": str(anchor),
                    "session_id": session_id,
                    "manifest_name": manifest_name,
                },
                ensure_ascii=False,
            )
        )
    else:
        # Shell export lines for `eval $(cl session start ...)`.
        typer.echo(f'export {ANCHOR_ENV}="{anchor}"')
        typer.echo(f'export {SESSION_ENV}="{session_id}"')


@app.command("show")
def show() -> None:
    """Print current anchor + session_id from env; exit non-zero if unset."""
    anchor = os.environ.get(ANCHOR_ENV, "").strip()
    sid = os.environ.get(SESSION_ENV, "").strip()
    if not anchor or not sid:
        typer.echo(
            f"cl session show: no active session ({ANCHOR_ENV} or {SESSION_ENV} unset).",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"{ANCHOR_ENV}={anchor}")
    typer.echo(f"{SESSION_ENV}={sid}")


@app.command("end")
def end(
    archive: bool = typer.Option(
        True,
        "--archive/--no-archive",
        help="Move session subdir from sessions/ to archived/.",
    ),
) -> None:
    """Emit `unset` lines for eval, optionally archive the session subdir."""
    anchor = os.environ.get(ANCHOR_ENV, "").strip()
    sid = os.environ.get(SESSION_ENV, "").strip()
    if archive and anchor and sid and is_valid_session_id(sid):
        try:
            anchor_path = Path(anchor)
            paths = SessionPaths(anchor=anchor_path, session_id=sid)
            src = paths.root
            if src.is_dir():
                dst_root = archived_root(anchor_path)
                dst_root.mkdir(parents=True, exist_ok=True)
                dst = dst_root / sid
                if not dst.exists():
                    shutil.move(str(src), str(dst))
        except OSError as e:
            typer.echo(f"cl session end: archive failed: {e}", err=True)

    typer.echo(f"unset {ANCHOR_ENV} {SESSION_ENV}")
