# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl ledger` — operator-interventions ledger (capture half).

Subcommands:
  capture  — append an unjudged candidate to the ledger (deduped). Usually
             invoked by a fleet signal (e.g. a worker observing a human closed
             its PR), not by hand. Promotion (adding the judgment line) is
             always manual — this command never judges and never promotes.
"""

from __future__ import annotations

import typer

from context_lifecycle.ledger import LedgerUnavailable, capture

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command("capture")
def capture_cmd(
    signal: str = typer.Argument(
        ..., help="Short kebab signal id, e.g. 'pr-closed-by-human'."
    ),
    context: str = typer.Argument(
        ..., help="One-line context, e.g. 'RepoGraph#42: add owns-edge validator'."
    ),
    date: str = typer.Option(
        "", "--date", help="ISO date for the entry (default: today). For tests/backfill."
    ),
) -> None:
    """Append an unjudged candidate to the ledger in the private manifest.

    Fail-soft: if the private manifest can't be resolved the command exits 2
    with a message (callers that fire this opportunistically should ignore that).
    Deduped: a candidate with the same signal+context that's still unpromoted is
    not re-added.
    """
    try:
        path, written = capture(signal, context, date=date or None)
    except LedgerUnavailable as exc:
        typer.echo(f"cl ledger capture: {exc}", err=True)
        raise typer.Exit(code=2)
    except ValueError as exc:
        typer.echo(f"cl ledger capture: {exc}", err=True)
        raise typer.Exit(code=1)

    if written:
        typer.echo(f"cl ledger capture: candidate recorded → {path}")
    else:
        typer.echo(f"cl ledger capture: already present (deduped) → {path}")
