# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl loop` — the PseudoOperator session-loop harness (Track B).

One config-parameterized engine replacing the per-repo copy-paste
``tools/loop/controller.py`` files. The repo's policy lives in its config
file's ``pseudo_operator:`` section (VF ``.context/config.yaml``,
OC ``.console/workers.yaml``); the engine consumes every field fail-closed.

Subcommands:
  run     — acquire the lock and drive the session loop (foreground; the
            launcher backgrounds it).
  status  — show lock/runtime state.
  stop    — write the stop flag; the loop exits after the current session.
  pause   — write the pause flag (operator-present signal): the loop idles
            without exiting until `resume`.
  resume  — remove the pause flag.
  signal  — inject a one-shot investigation task for the next session.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import typer

from context_lifecycle.pseudo_operator import (
    PseudoOperatorEngine,
    load_pseudo_operator_config,
)

app = typer.Typer(no_args_is_help=True, add_completion=False)

_CONFIG_OPT = typer.Option(
    ..., "--config", "-c", help="Repo config YAML holding the pseudo_operator section."
)


def _load(config: Path):
    try:
        return load_pseudo_operator_config(config)
    except (ValueError, OSError) as exc:
        typer.echo(f"config error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


@app.command("run")
def run_cmd(config: Path = _CONFIG_OPT) -> NoReturn:
    """Run the loop (foreground). Refuses to start on an invalid config."""
    cfg = _load(config)
    raise typer.Exit(code=PseudoOperatorEngine(cfg).run())


@app.command("status")
def status_cmd(config: Path = _CONFIG_OPT) -> None:
    """Show lock + runtime state for this repo's loop."""
    cfg = _load(config)
    if not cfg.lock_path.exists():
        typer.echo("No lock file — loop is not running.")
    else:
        try:
            d = json.loads(cfg.lock_path.read_text(encoding="utf-8"))
            typer.echo(
                f"Lock: pid={d.get('pid')} host={d.get('hostname')} "
                f"started={d.get('started')}"
            )
        except (OSError, json.JSONDecodeError) as exc:
            typer.echo(f"ERROR reading lock: {exc}")
    if cfg.pause_flag_path.exists():
        typer.echo("PAUSED (pause flag present).")
    if cfg.runtime_state_path.exists():
        try:
            typer.echo(cfg.runtime_state_path.read_text(encoding="utf-8").rstrip())
        except OSError as exc:
            typer.echo(f"ERROR reading runtime state: {exc}")


def _touch_flag(config: Path, flag_attr: str, label: str) -> None:
    cfg = _load(config)
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    flag = getattr(cfg, flag_attr)
    flag.touch()
    typer.echo(f"{label} flag written to {flag}.")


@app.command("stop")
def stop_cmd(config: Path = _CONFIG_OPT) -> None:
    """Signal the loop to stop after the current session."""
    _touch_flag(config, "stop_flag_path", "Stop")


@app.command("pause")
def pause_cmd(config: Path = _CONFIG_OPT) -> None:
    """Pause the loop (operator-present signal) — it idles without exiting."""
    _touch_flag(config, "pause_flag_path", "Pause")


@app.command("resume")
def resume_cmd(config: Path = _CONFIG_OPT) -> None:
    """Resume a paused loop."""
    cfg = _load(config)
    cfg.pause_flag_path.unlink(missing_ok=True)
    typer.echo("Pause flag removed.")


@app.command("signal")
def signal_cmd(
    task: str = typer.Argument(..., help="One-shot investigation task for the next session."),
    config: Path = _CONFIG_OPT,
) -> None:
    """Inject a one-shot task (consumed and deleted by the next session)."""
    cfg = _load(config)
    cfg.state_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": task,
        "injected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "injected_by": "cl loop signal",
        "consumed": False,
    }
    cfg.signal_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    typer.echo(f"Signal written to {cfg.signal_path}.")
