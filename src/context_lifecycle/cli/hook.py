"""`cl hook` subcommands. Wraps decision functions with stdin/stdout I/O.

Exit contract (Claude Code hook protocol):
  0  allow
  2  block (stderr surfaced to operator; JSON `{"decision":"block","reason":"..."}` on stdout)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import NoReturn

import typer

from context_lifecycle.errors import AnchorMissing, SessionNotStarted
from context_lifecycle.hooks.pre_tool_use import HookInput, evaluate_pre_tool_use
from context_lifecycle.hooks.stop import evaluate_stop
from context_lifecycle.models.config import CLConfig
from context_lifecycle.session.anchor import require_anchor_env
from context_lifecycle.session.ids import require_session_env
from context_lifecycle.session.paths import SessionPaths

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _session_marker_path(anchor: Path, session_id: str) -> Path:
    """Per-session marker file. Used by stop.py to detect fresh checkpoints."""
    # /tmp/cl_session_<sid>__<anchor-hash> — survives across hook invocations.
    digest = abs(hash(str(anchor))) % (10**10)
    return Path("/tmp") / f"cl_session_{session_id}_{digest}"


def _touch_marker(path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.touch()
    except OSError:
        pass


def _read_stdin_json() -> dict:
    raw = sys.stdin.read() or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _bootstrap_session() -> tuple[Path, str, SessionPaths, CLConfig]:
    """Common: read env, build SessionPaths, load config. Exits 2 on AnchorMissing."""
    try:
        anchor = require_anchor_env()
        session_id = require_session_env()
    except (AnchorMissing, SessionNotStarted) as e:
        # Hard error — surface message and exit 2 (blocks the tool call).
        print(json.dumps({"decision": "block", "reason": str(e)}, ensure_ascii=False))
        print(str(e), file=sys.stderr)
        raise typer.Exit(code=2)

    paths = SessionPaths(anchor=anchor, session_id=session_id)
    config = CLConfig.from_file(paths.config_file)
    return anchor, session_id, paths, config


@app.command("pre_tool_use")
def pre_tool_use() -> NoReturn:
    """Evaluate the PreToolUse hook. Reads JSON payload from stdin."""
    anchor, session_id, paths, config = _bootstrap_session()
    payload = _read_stdin_json()
    hook_input = HookInput.from_payload(payload)

    # Touch the per-session marker so `stop` can detect "checkpoint was written this session".
    _touch_marker(_session_marker_path(anchor, session_id))

    result = evaluate_pre_tool_use(paths=paths, config=config, hook_input=hook_input)

    for w in result.warnings:
        print(w.reason, file=sys.stderr)

    if result.is_block:
        print(json.dumps({"decision": "block", "reason": result.reason}, ensure_ascii=False))
        print(result.reason, file=sys.stderr)
        raise typer.Exit(code=2)
    raise typer.Exit(code=0)


@app.command("stop")
def stop() -> NoReturn:
    """Evaluate the Stop hook. Reads JSON payload from stdin (ignored for now)."""
    anchor, session_id, paths, config = _bootstrap_session()
    _ = _read_stdin_json()

    marker = _session_marker_path(anchor, session_id)
    report = evaluate_stop(paths=paths, config=config, session_marker=marker)

    if report.enforcement_message:
        print(report.enforcement_message, file=sys.stderr)
    for w in report.decision.warnings:
        print(f"ContextGuard warning: {w.reason}", file=sys.stderr)

    # Stop is structurally non-fatal per the bash reference.
    raise typer.Exit(code=0)
