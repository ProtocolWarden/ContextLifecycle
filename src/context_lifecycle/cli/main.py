"""`cl` Typer app — entry point for the CLI."""

from __future__ import annotations

import typer

from context_lifecycle.cli import hook as hook_cmd
from context_lifecycle.cli import session as session_cmd

app = typer.Typer(
    name="cl",
    help="ContextLifecycle CLI — manages cognition sessions, hooks, and anchors.",
    no_args_is_help=True,
    add_completion=False,
)

app.add_typer(hook_cmd.app, name="hook", help="Claude Code hook adapters (pre_tool_use, stop).")
app.add_typer(session_cmd.app, name="session", help="Session anchor lifecycle (start, show, end).")


if __name__ == "__main__":  # pragma: no cover
    app()
