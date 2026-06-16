# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl` Typer app — entry point for the CLI."""

from __future__ import annotations

import typer

from context_lifecycle.cli import context as context_cmd
from context_lifecycle.cli import hook as hook_cmd
from context_lifecycle.cli import ledger as ledger_cmd
from context_lifecycle.cli import reconcile as reconcile_cmd
from context_lifecycle.cli import session as session_cmd

app = typer.Typer(
    name="cl",
    help="ContextLifecycle CLI — manages cognition sessions, hooks, and anchors.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(hook_cmd.app, name="hook", help="Claude Code hook adapters (pre_tool_use, stop).")
app.add_typer(session_cmd.app, name="session", help="Session anchor lifecycle (start, show, end).")
app.add_typer(context_cmd.app, name="context", help="Session-boundary cognition (hydrate, capture, peek) for non-hook CLIs.")
app.add_typer(reconcile_cmd.app, name="reconcile", help="`.console/` reconciliation pass (check, prune, index).")
app.add_typer(ledger_cmd.app, name="ledger", help="Operator-interventions ledger (capture; promotion stays manual).")


if __name__ == "__main__":  # pragma: no cover
    app()
