# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl ledger` — operator-interventions ledger (capture half).

Subcommands:
  capture  — append an unjudged candidate to the ledger (deduped). Usually
             invoked by a fleet signal (e.g. a worker observing a human closed
             its PR), not by hand. This command never judges and never promotes.
  check    — surface candidates left unpromoted past a max age (the staleness
             gate). Exit 1 when stale candidates exist.
  observe  — cluster candidates by recurring signal; surface novel patterns
             awaiting a first human judgment. Counts, never judges.
  promote  — self-verifying auto-promote: promote each recurrence of a signal
             whose first judgment carries a live `[check: ref]`; flag refs that
             stopped resolving (regressions). Invents no judgment.
"""

from __future__ import annotations

from pathlib import Path

import typer

from context_lifecycle.ledger import LedgerUnavailable, capture
from context_lifecycle.ledger.check import DEFAULT_MAX_AGE_DAYS, check as run_check
from context_lifecycle.ledger.observe import (
    DEFAULT_MIN_COUNT,
    DEFAULT_WINDOW_DAYS,
    observe as run_observe,
)
from context_lifecycle.ledger.promote import promote as run_promote

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


@app.command("observe")
def observe_cmd(
    min_count: int = typer.Option(
        DEFAULT_MIN_COUNT, "--min-count", help="Min candidate lines for a signal to cluster."
    ),
    window_days: int = typer.Option(
        DEFAULT_WINDOW_DAYS, "--window-days", help="Only count candidates this recent."
    ),
) -> None:
    """List signals recurring across candidates — the patterns awaiting a first judgment.

    Makes no judgment; it only counts, skipping signals that already carry a
    verifiable promoted judgment (those belong to `promote`). Fail-soft on a
    missing private manifest. Exit 0 always — this is a nudge, not a gate.
    """
    try:
        clusters = run_observe(min_count=min_count, window_days=window_days)
    except LedgerUnavailable:
        typer.echo("cl ledger observe: no ledger to observe (private manifest unavailable)")
        return
    if not clusters:
        typer.echo("cl ledger observe: no recurring unjudged signals")
        return
    typer.echo(f"cl ledger observe: {len(clusters)} recurring signal(s) awaiting a judgment:")
    for c in clusters:
        typer.echo(f"  x{c.count}  {c.signal}  (latest {c.latest_date})")
    typer.echo("Encode a check + add `[check: ref]` to the judgment so recurrences self-verify.")


@app.command("promote")
def promote_cmd(
    repos_root: str = typer.Option(
        ...,
        "--repos-root",
        help="Directory holding managed repo checkouts (for resolving [check: ref]s).",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would promote without writing."
    ),
) -> None:
    """Auto-promote each recurrence of a signal whose judgment carries a live check.

    Promotes ONLY by re-verifying an existing human judgment's `[check: ref]` —
    never invents a judgment. A ref that stopped resolving is reported as a
    regression (exit 1) and its candidates are left untouched. Fail-soft on a
    missing private manifest.
    """
    try:
        outcome = run_promote(repos_root=Path(repos_root), write=not dry_run)
    except LedgerUnavailable:
        typer.echo("cl ledger promote: no ledger to promote (private manifest unavailable)")
        return

    verb = "would promote" if dry_run else "promoted"
    if outcome.promoted:
        typer.echo(f"cl ledger promote: {verb} {len(outcome.promoted)} recurrence(s):")
        for signal, ref, source_date in outcome.promoted:
            typer.echo(f"  ✓ {signal}  (judged {source_date}, [check: {ref}] live)")
    else:
        typer.echo("cl ledger promote: nothing to promote (no verifiable recurrences)")

    if outcome.regressed:
        typer.echo(f"cl ledger promote: {len(outcome.regressed)} encoded check(s) REGRESSED:")
        for signal, ref, detail in outcome.regressed:
            typer.echo(f"  ✗ {signal}  [check: {ref}] — {detail}")
        typer.echo("An encoded judgment rotted — a human must re-encode or drop it.")
        raise typer.Exit(code=1)
