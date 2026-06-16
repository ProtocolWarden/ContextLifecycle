# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl ledger` — operator-interventions ledger (capture half).

Subcommands:
  capture  — append an unjudged candidate to the ledger (deduped). Usually
             invoked by a fleet signal (e.g. a worker observing a human closed
             its PR), not by hand. Promotion (adding the judgment line) is
             always manual — this command never judges and never promotes.
  check    — surface candidates left unpromoted past a max age (closes the
             capture→promote loop). Exit 1 when stale candidates exist.
"""

from __future__ import annotations

import typer

from context_lifecycle.ledger import LedgerUnavailable, capture
from context_lifecycle.ledger.check import DEFAULT_MAX_AGE_DAYS, check as run_check

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


@app.command("check")
def check_cmd(
    max_age_days: int = typer.Option(
        DEFAULT_MAX_AGE_DAYS, "--max-age-days", help="Flag candidates older than this."
    ),
) -> None:
    """List candidates left unpromoted past --max-age-days. Exit 1 if any.

    Fail-soft on a missing private manifest (exit 0, nothing to check) so this is
    safe to call opportunistically from a hook. A real stale candidate exits 1 so
    it can gate or warn — write the judgment line, or drop the entry.
    """
    try:
        stale = run_check(max_age_days=max_age_days)
    except LedgerUnavailable:
        typer.echo("cl ledger check: no ledger to check (private manifest unavailable)")
        return
    if not stale:
        typer.echo("cl ledger check: OK — no candidates past the age threshold")
        return
    typer.echo(f"cl ledger check: {len(stale)} unpromoted candidate(s) past {max_age_days}d:")
    for c in stale:
        typer.echo(f"  +{c.age_days}d  {c.date}  {c.signal} — {c.context}")
    typer.echo("Promote (add the judgment line) or drop each, then re-run.")
    raise typer.Exit(code=1)
