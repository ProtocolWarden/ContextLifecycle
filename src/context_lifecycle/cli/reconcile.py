# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl reconcile` — Layer B of the `.console/` reconciliation chain (spec §3).

Subcommands:
  check  — the I1 gate; read-only; exit non-zero on DOC GAP or scrub-target leak.
  prune  — move-and-trim completed history; dry-run by default, `--apply` mutates.
  index  — generate the status dashboard over local clones.
"""

from __future__ import annotations

from pathlib import Path

import typer

from context_lifecycle.reconcile.check import format_report, run_check
from context_lifecycle.reconcile.index import (
    build_index,
    load_public_repo_names,
    render_index,
)
from context_lifecycle.reconcile.lock import PruneLockHeld
from context_lifecycle.reconcile.prune import (
    DEFAULT_RECENT_N,
    PruneRefused,
    apply_plan,
    build_plan,
    format_plan,
)
from context_lifecycle.reconcile.privacy import PrivateArchiveUnavailable

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("check")
def check_cmd(
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repo root holding .console/reconcile.yaml (default: cwd)."
    ),
) -> None:
    """The I1 gate (read-only). Exit non-zero on any DOC GAP or scrub-target leak."""
    result = run_check(repo.resolve())
    typer.echo(format_report(result))
    if not result.green:
        raise typer.Exit(code=1)


@app.command("prune")
def prune_cmd(
    repo: Path = typer.Option(
        Path("."), "--repo", help="Repo root to prune (default: cwd)."
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Mutate files. Without this flag, prune is a dry-run."
    ),
    recent_n: int = typer.Option(
        DEFAULT_RECENT_N, "--recent", help="Most-recent log entries to retain in source."
    ),
) -> None:
    """Move completed history to the private archive + trim source. Runs only when check is green."""
    repo_root = repo.resolve()
    try:
        plan = build_plan(repo_root, recent_n=recent_n)
    except PruneRefused as e:
        typer.echo(f"cl reconcile prune: {e}", err=True)
        raise typer.Exit(code=1)
    except PrivateArchiveUnavailable as e:
        typer.echo(f"cl reconcile prune: {e}", err=True)
        raise typer.Exit(code=2)

    if apply:
        try:
            plan = apply_plan(repo_root, plan, recent_n=recent_n)
        except PruneLockHeld as e:
            typer.echo(f"cl reconcile prune: {e}", err=True)
            raise typer.Exit(code=3)
    typer.echo(format_plan(plan, applied=apply))


@app.command("index")
def index_cmd(
    clones_root: Path = typer.Option(
        ..., "--clones-root", help="Directory containing local repo clones."
    ),
    manifest: Path = typer.Option(
        ..., "--manifest", help="platform_manifest.yaml defining the public repo set."
    ),
    out: Path = typer.Option(
        None, "--out", help="Write dashboard here instead of stdout."
    ),
    check: bool = typer.Option(
        False,
        "--check",
        help="With --out: don't write — exit non-zero if the file is missing or stale.",
    ),
) -> None:
    """Generate the status dashboard. Public repos itemized; private repos as an opaque count."""
    if check and out is None:
        typer.echo("cl reconcile index: --check requires --out", err=True)
        raise typer.Exit(code=2)
    public = load_public_repo_names(manifest.resolve())
    result = build_index(clones_root.resolve(), public)
    rendered = render_index(result)
    if out is not None:
        out = out.resolve()
        if check:
            current = out.read_text(encoding="utf-8") if out.is_file() else None
            if current == rendered:
                typer.echo(f"cl reconcile index: {out} is up to date")
            else:
                state = "missing" if current is None else "stale"
                typer.echo(
                    f"cl reconcile index: {out} is {state} — regenerate with "
                    f"`cl reconcile index --out {out} ...`",
                    err=True,
                )
                raise typer.Exit(code=1)
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(rendered, encoding="utf-8")
            typer.echo(f"cl reconcile index: wrote {out}")
    else:
        typer.echo(rendered)
