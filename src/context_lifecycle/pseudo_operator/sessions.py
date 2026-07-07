# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Session layer for the PseudoOperator engine: CLI resolution, CL anchoring,
env construction, backend command shapes, and the bounded session spawn.

Split from engine.py — the engine owns the loop; this owns one session.
The ``SessionMixin`` methods reference engine attributes (``cfg``, ``_log``,
``_backends``, ``_cl_*``) and are mixed into ``PseudoOperatorEngine``.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

def _resolve_command(command: str) -> str | None:
    resolved = shutil.which(command)
    if resolved is not None:
        return resolved
    home = Path.home()
    candidates: list[Path] = []
    if command in {"claude", "codex"}:
        candidates += [home / ".local" / "bin" / command, home / "bin" / command]
        if command == "codex":
            candidates += sorted((home / ".nvm" / "versions" / "node").glob("*/bin/codex"))
    elif command == "cl":
        cl_home = os.environ.get("CL_HOME")
        if not cl_home:
            try:
                settings = home / ".claude" / "settings.json"
                if settings.exists():
                    cl_home = json.loads(settings.read_text(encoding="utf-8")).get("env", {}).get("CL_HOME", "")
            except Exception:  # noqa: BLE001
                cl_home = ""
        if cl_home:
            candidates.append(Path(cl_home) / "bin" / "cl")
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _command_available(command: str) -> bool:
    return _resolve_command(command) is not None



_HOOK_TIMEOUT_S = 300
_ENV_FILE_TIMEOUT_S = 60
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _env_file_vars(env_file: Path) -> dict[str, str]:
    """Variables an env file defines, resolved with real shell semantics.

    Env files in the wild use ``export``, quoting, and command substitution
    (e.g. ``GITHUB_TOKEN=$(gh auth token)``) — the same files are ``source``d
    by the repos' shell wrappers. A literal line parse hands sessions the
    unexpanded ``$(...)`` string as a credential, so source the file in a
    throwaway shell and read back the resulting environment. Falls back to
    the literal parse if the shell fails (missing bash, timeout, bad file).
    """
    try:
        proc = subprocess.run(
            ["bash", "-c", 'set -a; . "$1" >/dev/null 2>&1; env -0', "bash", str(env_file)],
            capture_output=True,
            timeout=_ENV_FILE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _env_file_vars_literal(env_file)
    if proc.returncode != 0 or not proc.stdout:
        return _env_file_vars_literal(env_file)
    result: dict[str, str] = {}
    for entry in proc.stdout.split(b"\0"):
        if not entry:
            continue
        key_b, sep, value_b = entry.partition(b"=")
        if not sep:
            continue
        key = key_b.decode("utf-8", errors="replace")
        if _ENV_KEY_RE.match(key):
            result[key] = value_b.decode("utf-8", errors="replace")
    return result


def _env_file_vars_literal(env_file: Path) -> dict[str, str]:
    """Fallback: literal ``KEY=VALUE`` line parse (no expansion)."""
    result: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):]
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            result[k.strip()] = v.strip().strip("'\"")
    return result


class SessionMixin:
    """CL anchoring + session spawn + hook execution, mixed into PseudoOperatorEngine."""

    def _run_hook(self, name: str, argv: list[str], extra_arg: str | None = None) -> str | None:
        """Best-effort hook execution; returns stdout or None on failure."""
        if not argv:
            return None
        cmd = [*argv, extra_arg] if extra_arg is not None else list(argv)
        try:
            out = subprocess.run(
                cmd,
                cwd=self.cfg.repo_root,
                capture_output=True,
                text=True,
                timeout=_HOOK_TIMEOUT_S,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            self._log(f"hook {name} failed to run: {exc}")
            return None
        if out.returncode != 0:
            self._log(f"hook {name} exited {out.returncode}: {out.stderr.strip()[:200]}")
            return None
        err = out.stderr.strip()
        if err:
            # Hooks log actions (e.g. self-update pull + watcher restart) via
            # logging → stderr; surface the tail so deploys are visible in the
            # loop log instead of silently discarded on success.
            self._log(f"hook {name}: {err.splitlines()[-1][:200]}")
        return out.stdout

    def _seed_cooldowns(self, cooldowns: dict[str, datetime | None]) -> None:
        out = self._run_hook("seed_cooldowns", self.cfg.hooks.seed_cooldowns)
        if not out:
            return
        try:
            seeded = json.loads(out)
        except json.JSONDecodeError:
            self._log("seed_cooldowns hook produced non-JSON output — ignored.")
            return
        now = datetime.now(timezone.utc)
        for backend, iso in seeded.items():
            if backend not in cooldowns or not iso:
                continue
            try:
                dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
            except ValueError:
                continue
            if dt > now:
                cooldowns[backend] = dt
                self._log(f"Seeded {backend} cooldown until {iso} (hook).")


    def _anchor_via_cl(self, env: dict[str, str]) -> None:
        try:
            out = subprocess.run(
                [_resolve_command("cl") or "cl", "session", "start"],
                cwd=self.cfg.repo_root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return
        if out.returncode != 0:
            return
        for line in out.stdout.splitlines():
            line = line.strip()
            if line.startswith("export ") and "=" in line:
                key, _, val = line[len("export "):].partition("=")
                env[key.strip()] = val.strip().strip("'\"")

    def _end_cl_session(self, anchor_vars: dict[str, str]) -> None:
        if not anchor_vars.get("CL_SESSION_ID"):
            return
        env = os.environ.copy()
        env.update(anchor_vars)
        try:
            subprocess.run(
                [_resolve_command("cl") or "cl", "session", "end", "--archive"],
                cwd=self.cfg.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _cl_session_boundary(self, backend: str, env: dict[str, str]) -> bool:
        # claude/opus run ContextGuard hooks per tool call; codex/aider need
        # session-boundary hydrate/capture. Gated on an active anchor.
        return backend not in ("claude", "opus") and bool(env.get("CL_ANCHOR"))

    def _cl_hydrate(self, backend: str, env: dict[str, str], iteration: int, prompt: str) -> str:
        if not self._cl_session_boundary(backend, env):
            return prompt
        work_item = json.dumps(
            {"loop": self.cfg.loop_name, "iteration": iteration, "backend": backend},
            ensure_ascii=False,
        )
        try:
            out = subprocess.run(
                [
                    _resolve_command("cl") or "cl",
                    "context",
                    "hydrate",
                    "--lineage",
                    self._lineage,
                    "--work-item",
                    work_item,
                ],
                cwd=self.cfg.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return prompt
        if out.returncode != 0 or not out.stdout.strip():
            return prompt
        return (
            "# Prior session context (ContextLifecycle hydrate)\n```json\n"
            + out.stdout.strip()
            + "\n```\n\n"
            + prompt
        )

    def _cl_capture(
        self, backend: str, env: dict[str, str], iteration: int, exit_code: int, log_path: Path
    ) -> None:
        if not self._cl_session_boundary(backend, env):
            return
        result = json.dumps({
                "loop": self.cfg.loop_name,
                "iteration": iteration,
                "backend": backend,
                "exit_code": exit_code,
                "log": str(log_path),
            },
            ensure_ascii=False,
        )
        try:
            subprocess.run(
                [
                    _resolve_command("cl") or "cl",
                    "context",
                    "capture",
                    "--lineage",
                    self._lineage,
                    "--result",
                    result,
                ],
                cwd=self.cfg.repo_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def _session_env(self, name: str, anchor_vars: dict[str, str]) -> dict[str, str]:
        env = os.environ.copy()
        env_file = self.cfg.resolved_env_file
        if env_file is not None and env_file.exists():
            for k, v in _env_file_vars(env_file).items():
                env.setdefault(k, v)
        env.update(anchor_vars)
        cli = self._cli_for(self._backends[name])
        executable = _resolve_command(cli)
        if executable is None:
            return env
        executable_path = Path(executable)
        path_candidates = [str(executable_path.parent)]
        resolved_parent = str(executable_path.resolve().parent)
        if resolved_parent not in path_candidates:
            path_candidates.append(resolved_parent)
        path_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
        prepend = [part for part in path_candidates if part not in path_parts]
        if prepend:
            env["PATH"] = os.pathsep.join([*prepend, *path_parts]) if path_parts else os.pathsep.join(prepend)
        return env

    def _session_command(self, name: str, prompt: str) -> list[str]:
        spec = self._backends[name]
        if spec.invocation_kind == "claude":
            executable = _resolve_command("claude") or "claude"
            return [
                executable,
                "-p",
                prompt,
                "--model",
                spec.model,
                "--effort",
                spec.effort,
                "--dangerously-skip-permissions",
                "--output-format",
                "text",
            ]
        executable = _resolve_command("codex") or "codex"
        return [
            executable,
            "exec",
            "--dangerously-bypass-approvals-and-sandbox",
            "--cd",
            str(self.cfg.repo_root),
            "--model",
            spec.model,
            "-c",
            f'model_reasoning_effort="{spec.effort}"',
            prompt,
        ]

    def _session_log_path(self, iteration: int, name: str) -> Path:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return self.cfg.session_log_dir / f"iter_{iteration:04d}_{ts}_{name}.log"

    def run_session(
        self, iteration: int, name: str, anchor_vars: dict[str, str]
    ) -> tuple[int, Path]:
        prompt = self.cfg.prompt_path.read_text(encoding="utf-8")
        env = self._session_env(name, anchor_vars)
        prompt = self._cl_hydrate(name, env, iteration, prompt)
        cmd = self._session_command(name, prompt)

        self.cfg.session_log_dir.mkdir(parents=True, exist_ok=True)
        session_log = self._session_log_path(iteration, name)
        self._log(
            f"Spawning {name} session (prompt: {self.cfg.prompt_path.name}, "
            f"{len(prompt)} chars) → {session_log.name}"
        )
        with session_log.open("w") as log_fh:
            proc = subprocess.Popen(
                cmd, cwd=self.cfg.repo_root, env=env, stdout=log_fh, stderr=log_fh
            )
            try:
                proc.wait(timeout=self.cfg.session_timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                self._log(
                    f"[WARN] Session exceeded {self.cfg.session_timeout_seconds}s timeout — "
                    f"killed. Log: {session_log.name}"
                )
                self._cl_capture(name, env, iteration, -1, session_log)
                self._session_end_hook(iteration, name, -1, session_log)
                return -1, session_log
        self._cl_capture(name, env, iteration, proc.returncode, session_log)
        self._session_end_hook(iteration, name, proc.returncode, session_log)
        return proc.returncode, session_log

    def _session_end_hook(self, iteration: int, name: str, rc: int, log_path: Path) -> None:
        self._run_hook(
            "session_end",
            self.cfg.hooks.session_end,
            json.dumps({
                    "iteration": iteration,
                    "backend": name,
                    "returncode": rc,
                    "log_path": str(log_path),
                },
                ensure_ascii=False,
            ),
        )



__all__ = ["SessionMixin"]
