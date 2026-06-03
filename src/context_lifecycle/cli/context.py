# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl context` — session-boundary cognition for non-hook CLIs.

Claude Code uses per-tool guard hooks. CLIs without that mechanism (aider; codex
until a native plugin lands) integrate at session edges instead: hydrate prior
context into the opening prompt, then capture the result on exit. This command
group exposes the existing :mod:`context_lifecycle.lifecycle` API to shell
launch wrappers.

JSON inputs accept a literal string, ``@path`` to read a file, or ``-`` for stdin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from context_lifecycle import lifecycle

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _load_json(arg: str) -> Any:
    """Parse a JSON arg: a literal string, ``@file``, or ``-`` (stdin)."""
    if arg == "-":
        return json.load(sys.stdin)
    if arg.startswith("@"):
        return json.loads(Path(arg[1:]).read_text(encoding="utf-8"))
    return json.loads(arg)


@app.command("hydrate")
def hydrate_cmd(
    lineage: str = typer.Option(..., "--lineage", help="Lineage id for this work."),
    work_item: str = typer.Option("{}", "--work-item", help="JSON work item, @file, or -."),
) -> None:
    """Hydrate prior cognition for a lineage; prints the HydratedContext as JSON."""
    ctx = lifecycle.hydrate(lineage, _load_json(work_item))
    typer.echo(json.dumps(ctx.to_dict(), indent=2, ensure_ascii=False))


@app.command("capture")
def capture_cmd(
    lineage: str = typer.Option(..., "--lineage", help="Lineage id for this work."),
    result: str = typer.Option("{}", "--result", help="JSON result, @file, or -."),
) -> None:
    """Capture a result into the lineage's cognition (no-op-safe; boundary-checked)."""
    lifecycle.capture(lineage, _load_json(result))


@app.command("peek")
def peek_cmd(
    work_item: str = typer.Option("{}", "--work-item", help="JSON work item, @file, or -."),
) -> None:
    """Print prior cognition for a work item, if any (nothing if none)."""
    data = lifecycle.peek(_load_json(work_item))
    if data is not None:
        typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


@app.command("init")
def init_cmd(
    repo: Path = typer.Option(
        Path("."), "--repo", help="Target repo root to scaffold into (default: cwd)."
    ),
) -> None:
    """Scaffold the context-injection engine + state into a repo (idempotent).

    Copies the engine into ``<repo>/.context/.engine/`` (refreshed each run) and
    creates ``routes.yaml`` / ``docs/inject/`` / ``.context/knowledge/`` only when
    absent. Re-runnable during provisioning; never clobbers authored routes/docs.
    """
    from context_lifecycle.context_engine.scaffold import init_context

    report = init_context(repo.resolve())
    for rel in report.created:
        typer.echo(f"  created   {rel}")
    for rel in report.refreshed:
        typer.echo(f"  refreshed {rel}")
    for rel in report.skipped:
        typer.echo(f"  kept      {rel}")
    typer.echo(
        f"cl context init: {len(report.created)} created, "
        f"{len(report.refreshed)} refreshed, {len(report.skipped)} kept "
        f"in {repo.resolve()}"
    )
