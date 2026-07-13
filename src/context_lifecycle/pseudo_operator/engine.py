# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""PseudoOperatorEngine — the shared session-loop harness (Track B).

Mechanism lives here; policy arrives via ``PseudoOperatorConfig``. Ported from
the two live controllers with the hardened (Track A7) semantics as the base:

- atomic O_CREAT|O_EXCL lock with hostname-aware stale reclaim
- hard per-session wall timeout
- ENFORCED iteration + consecutive-failure caps (config, never unlimited)
- backend cooldown/fallback ladder with rate-limit reset parsing
- ContextLifecycle anchoring (session start/end, hydrate/capture for
  non-hook backends) — shelled through the ``cl`` CLI, same code path the
  old controllers used
- away/lazy trigger: a pause flag (``cl loop pause``) makes the engine idle
  without exiting — the operator-present signal from the spec's §4.4
- repo-specific extensions via hook commands (see config.HookCommands)
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import BackendSpec, PseudoOperatorConfig
from .sessions import SessionMixin, _command_available, _resolve_command  # noqa: F401
from .limits import (
    GLOBAL_CLAUDE_LIMIT_RE,
    RATE_LIMIT_BUFFER,
    classify_limit_kind,
    parse_rate_limit_reset,
)

logger = logging.getLogger(__name__)



class PseudoOperatorEngine(SessionMixin):
    """One repo's session loop, driven entirely by its config."""

    def __init__(self, cfg: PseudoOperatorConfig) -> None:
        self.cfg = cfg
        self._stop = False
        self._backends: dict[str, BackendSpec] = {b.name: b for b in cfg.backends}
        self._priority: list[str] = [b.name for b in cfg.backends]
        self._lineage = f"{cfg.loop_name}-loop"
        # Per-backend limit metadata from the last cooldown, for the runtime
        # state file (the OperatorConsole pane renders limit kind + model).
        self._limit_meta: dict[str, dict[str, str | None]] = {}
        self._sleeping_until: str | None = None
        # Trust-anchor status set by the CLI (Track C): ok | drift | unsigned.
        self.signed_status: str = "unsigned"
        cfg.state_path.mkdir(parents=True, exist_ok=True)

    # ── logging ───────────────────────────────────────────────────────────
    def _log(self, msg: str) -> None:
        line = f"[{self._ts()}] {msg}"
        print(line, flush=True)
        log_file = self.cfg.resolved_log_file
        if log_file is not None:
            try:
                log_file.parent.mkdir(parents=True, exist_ok=True)
                with log_file.open("a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    @staticmethod
    def _ts() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ── locking (atomic + hostname-aware, Track A7 semantics) ─────────────
    def _lock_payload(self) -> str:
        return json.dumps({
                "pid": os.getpid(),
                "started": self._ts(),
                "hostname": socket.gethostname(),
                "purpose": f"pseudo_operator:{self.cfg.loop_name}",
                "repo_root": str(self.cfg.repo_root),
            },
            indent=2,
            ensure_ascii=False,
        )

    def _lock_holder_is_live(self, d: dict) -> bool | None:
        pid = d.get("pid")
        if not pid:
            return None
        holder_host = d.get("hostname")
        if holder_host and holder_host != socket.gethostname():
            self._log(
                f"Lock written by host {holder_host!r} (we are {socket.gethostname()!r}) — "
                "cannot verify liveness cross-host; treating as HELD."
            )
            return True
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def acquire_lock(self) -> bool:
        lock = self.cfg.lock_path
        for _ in range(3):
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    d = json.loads(lock.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self._log("Malformed lock file — reclaiming.")
                    lock.unlink(missing_ok=True)
                    continue
                if self._lock_holder_is_live(d) is False:
                    self._log(f"Stale lock (pid={d.get('pid')} dead) — reclaiming.")
                    lock.unlink(missing_ok=True)
                    continue
                self._log(
                    f"Lock held by pid={d.get('pid')} on {d.get('hostname')} "
                    f"(started {d.get('started')}) — aborting."
                )
                return False
            with os.fdopen(fd, "w") as fh:
                fh.write(self._lock_payload())
            return True
        self._log("Could not acquire lock after retries — aborting.")
        return False

    def release_lock(self) -> None:
        self.cfg.lock_path.unlink(missing_ok=True)
        self.cfg.runtime_state_path.unlink(missing_ok=True)

    # ── backend ladder ────────────────────────────────────────────────────
    @staticmethod
    def _cli_for(backend: BackendSpec) -> str:
        return "claude" if backend.invocation_kind == "claude" else "codex"

    def _backend_available(self, name: str, cooldowns: dict[str, datetime | None]) -> bool:
        spec = self._backends[name]
        until = cooldowns.get(name)
        return _command_available(self._cli_for(spec)) and (
            until is None or datetime.now(timezone.utc) >= until
        )

    def _clear_expired_cooldowns(self, cooldowns: dict[str, datetime | None]) -> None:
        now = datetime.now(timezone.utc)
        for backend, until in list(cooldowns.items()):
            if until is not None and now >= until:
                cooldowns[backend] = None
                self._log(f"{backend.capitalize()} cooldown expired — backend runnable again.")

    def _select_backend(self, cooldowns: dict[str, datetime | None]) -> str | None:
        for name in self._priority:
            if self._backend_available(name, cooldowns):
                return name
        if not any(_command_available(self._cli_for(b)) for b in self.cfg.backends):
            raise SystemExit("No configured backend CLI is available on PATH.")
        return None

    def _next_runnable_fallback(
        self, name: str, cooldowns: dict[str, datetime | None]
    ) -> str | None:
        try:
            start_idx = self._priority.index(name)
        except ValueError:
            start_idx = -1
        for candidate in self._priority[start_idx + 1 :]:
            if self._backend_available(candidate, cooldowns):
                return candidate
        return None

    def _next_backend_reset(self, cooldowns: dict[str, datetime | None]) -> datetime | None:
        now = datetime.now(timezone.utc)
        future = [dt for dt in cooldowns.values() if dt is not None and dt > now]
        return min(future) if future else None

    def _sleep_until_backend_reset(self, cooldowns: dict[str, datetime | None]) -> bool:
        reset_dt = self._next_backend_reset(cooldowns)
        if reset_dt is None:
            return False
        delay = max(
            60,
            int((reset_dt - datetime.now(timezone.utc)).total_seconds()) + RATE_LIMIT_BUFFER,
        )
        self._log(
            f"All available backends are cooling down until "
            f"{reset_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} UTC; sleeping {delay}s ({delay // 60}m)"
        )
        self.cfg.schedule_path.unlink(missing_ok=True)
        self._sleeping_until = reset_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            self.interruptible_sleep(delay)
        finally:
            self._sleeping_until = None
        return True

    def _handle_backend_limit(
        self, name: str, session_log: Path, cooldowns: dict[str, datetime | None]
    ) -> bool:
        reset_dt, log_text = parse_rate_limit_reset(session_log, name)
        if reset_dt is None:
            return False
        cooldowns[name] = reset_dt
        spec = self._backends[name]
        limit_kind, model = classify_limit_kind(log_text, model=spec.model)
        self._limit_meta[name] = {"limit_kind": limit_kind, "model": model}
        # Global claude limits (5h session cap, account limit) apply to every
        # claude-CLI backend — cool them all so the ladder skips straight past.
        if (
            spec.invocation_kind == "claude"
            and log_text
            and GLOBAL_CLAUDE_LIMIT_RE.search(log_text)
        ):
            for other, other_spec in self._backends.items():
                if other != name and other_spec.invocation_kind == "claude":
                    cooldowns[other] = reset_dt
                    self._log(
                        f"Global Claude limit (session/account) — {other} also on cooldown."
                    )
        self._log(
            f"{name.capitalize()} limit ({limit_kind}) — cooling down until "
            f"{reset_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} UTC."
        )
        self._run_hook(
            "on_cooldown",
            self.cfg.hooks.on_cooldown,
            json.dumps({
                    "backend": name,
                    "reset_at": reset_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "limit_kind": limit_kind,
                    "model": model,
                },
                ensure_ascii=False,
            ),
        )
        return True

    # ── adaptive delay ────────────────────────────────────────────────────
    def _schedule_override(self) -> tuple[int | None, str | None]:
        try:
            if self.cfg.schedule_path.exists():
                s = json.loads(self.cfg.schedule_path.read_text(encoding="utf-8"))
                if isinstance(s.get("delay_s"), int) and s["delay_s"] > 0:
                    return s["delay_s"], s.get("reason", "session-written schedule")
                state = s.get("state")
                if isinstance(state, str):
                    return None, state
        except Exception:  # noqa: BLE001
            pass
        return None, None

    def _current_phase(self) -> str:
        policy = self.cfg.delay
        if not policy.status_glob:
            return "unknown"
        try:
            dirs = [
                d
                for d in self.cfg.repo_root.glob(policy.status_glob)
                if d.is_dir() and (d / "run_status.json").exists()
            ]
        except OSError:
            return "unknown"
        if not dirs:
            return "unknown"
        newest = max(dirs, key=lambda d: (d / "run_status.json").stat().st_mtime)
        try:
            return json.loads((newest / "run_status.json").read_text(encoding="utf-8")).get(
                "current_phase", "unknown"
            )
        except Exception:  # noqa: BLE001
            return "unknown"

    def _run_status(self) -> str | None:
        policy = self.cfg.delay
        if not policy.status_glob:
            return None
        try:
            dirs = [
                d
                for d in self.cfg.repo_root.glob(policy.status_glob)
                if d.is_dir() and (d / "run_status.json").exists()
            ]
            if not dirs:
                return None
            newest = max(dirs, key=lambda d: (d / "run_status.json").stat().st_mtime)
            return json.loads((newest / "run_status.json").read_text(encoding="utf-8")).get("status")
        except Exception:  # noqa: BLE001
            return None

    def get_delay(self) -> int:
        override, state_or_reason = self._schedule_override()
        if override is not None:
            self._log(f"Schedule override: {override}s ({state_or_reason})")
            return override
        policy = self.cfg.delay
        if policy.kind == "schedule_state" and state_or_reason:
            delay = policy.state_delays.get(state_or_reason, policy.default_delay_seconds)
            self._log(f"State '{state_or_reason}' → {delay}s delay")
            return delay
        if policy.kind == "phase_status":
            phase = self._current_phase()
            delay = policy.phase_delays.get(phase, policy.default_delay_seconds)
            self._log(f"Phase '{phase}' → {delay}s delay")
            return delay
        return policy.default_delay_seconds

    def interruptible_sleep(self, seconds: int, *, poll_completion: bool = False) -> None:
        was_running = poll_completion and self._run_status() not in ("completed", "failed", None)
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not self._stop and not self.cfg.stop_flag_path.exists():
            time.sleep(min(5.0, deadline - time.monotonic()))
            if was_running and self._run_status() in ("completed", "failed"):
                self._log("Run reached a terminal status — waking early.")
                return

    # ── runtime state ─────────────────────────────────────────────────────
    def write_runtime_state(
        self, cooldowns: dict[str, datetime | None], runnable: str | None
    ) -> None:
        state = {
            "updated": self._ts(),
            "loop": self.cfg.loop_name,
            "preferred_backend": self._priority[0] if self._priority else None,
            "runnable_backend": runnable,
            "backend_cooldowns": {
                backend: dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt is not None else None
                for backend, dt in cooldowns.items()
            },
            "backend_limit_kinds": {
                backend: dict(meta) for backend, meta in self._limit_meta.items()
            },
            "sleeping_until_utc": self._sleeping_until,
            "signed_status": self.signed_status,
            "paused": self.cfg.pause_flag_path.exists(),
        }
        try:
            self.cfg.runtime_state_path.write_text(
                json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass

    # ── main loop ─────────────────────────────────────────────────────────
    def _handle_signal(self, signum: int, _frame: object) -> None:
        self._stop = True
        self._log(f"Signal {signum} received — stop flag set; finishing current session.")

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)

        if not self.acquire_lock():
            return 1

        anchor_vars: dict[str, str] = {}
        iteration = 0
        consecutive_failures = 0
        try:
            self.cfg.stop_flag_path.unlink(missing_ok=True)
            self.cfg.schedule_path.unlink(missing_ok=True)
            self.cfg.runtime_state_path.unlink(missing_ok=True)
            self._log(
                f"PseudoOperator[{self.cfg.loop_name}] started. pid={os.getpid()} "
                f"caps: iterations={self.cfg.max_iterations} "
                f"consecutive_failures={self.cfg.max_consecutive_failures} "
                f"session_timeout={self.cfg.session_timeout_seconds}s"
            )
            self._anchor_via_cl(anchor_vars)
            if anchor_vars.get("CL_SESSION_ID"):
                self._log(f"Anchored: CL_SESSION_ID={anchor_vars['CL_SESSION_ID']}")
            else:
                self._log("Running unanchored (cl unavailable or manifest not registered)")

            cooldowns: dict[str, datetime | None] = {name: None for name in self._priority}
            self._seed_cooldowns(cooldowns)

            while not self._stop and not self.cfg.stop_flag_path.exists():
                # Away/lazy trigger (spec §4.4): the pause flag is the
                # operator-present signal — idle without exiting.
                if self.cfg.pause_flag_path.exists():
                    self.write_runtime_state(cooldowns, None)
                    self._log("Paused (pause flag present) — idling 60s.")
                    self.interruptible_sleep(60)
                    continue
                if iteration >= self.cfg.max_iterations:
                    self._log(
                        f"[GUARD] Max iterations per launch reached "
                        f"({self.cfg.max_iterations}) — stopping. Relaunch to continue."
                    )
                    break
                iteration += 1
                self._log(f"--- Iteration {iteration} ---")

                self._run_hook("pre_iteration", self.cfg.hooks.pre_iteration)

                self._clear_expired_cooldowns(cooldowns)
                if self.cfg.hooks.budget_guard:
                    self._budget_guard(cooldowns)
                backend = self._select_backend(cooldowns)
                self.write_runtime_state(cooldowns, backend)
                if backend is None:
                    if self._sleep_until_backend_reset(cooldowns):
                        self.write_runtime_state(cooldowns, None)
                        continue
                    self._log("No runnable backend is currently available.")
                    self.write_runtime_state(cooldowns, None)
                    break

                rc, session_log = self.run_session(iteration, backend, anchor_vars)
                self._log(f"{backend.capitalize()} session exited rc={rc}")
                if self._stop or self.cfg.stop_flag_path.exists():
                    break

                backend_limited = rc != 0 and self._handle_backend_limit(
                    backend, session_log, cooldowns
                )
                if rc == 0:
                    consecutive_failures = 0
                elif not backend_limited:
                    consecutive_failures += 1
                    if consecutive_failures >= self.cfg.max_consecutive_failures:
                        self._log(
                            f"[GUARD] {consecutive_failures} consecutive non-rate-limit "
                            f"session failures — stopping "
                            f"(cap {self.cfg.max_consecutive_failures}). "
                            "Investigate the session logs before relaunching."
                        )
                        break

                if rc != 0 and backend_limited:
                    self.write_runtime_state(cooldowns, None)
                    alternate = self._next_runnable_fallback(backend, cooldowns)
                    while alternate is not None:
                        self._log(
                            f"{backend.capitalize()} is cooling down; "
                            f"running immediate {alternate.capitalize()} fallback."
                        )
                        self.write_runtime_state(cooldowns, alternate)
                        rc, session_log = self.run_session(iteration, alternate, anchor_vars)
                        backend = alternate
                        self._log(f"{alternate.capitalize()} fallback session exited rc={rc}")
                        if self._stop or self.cfg.stop_flag_path.exists():
                            break
                        if rc != 0 and self._handle_backend_limit(
                            backend, session_log, cooldowns
                        ):
                            self.write_runtime_state(cooldowns, None)
                            alternate = self._next_runnable_fallback(backend, cooldowns)
                            continue
                        break
                    else:
                        if self._sleep_until_backend_reset(cooldowns):
                            continue

                delay = self.get_delay()
                self.cfg.schedule_path.unlink(missing_ok=True)
                self._log(f"Sleeping {delay}s ...")
                self.interruptible_sleep(
                    delay,
                    poll_completion=(
                        self.cfg.delay.kind == "phase_status"
                        and delay > self.cfg.delay.default_delay_seconds
                    ),
                )
        finally:
            self._end_cl_session(anchor_vars)
            self.release_lock()
            self._log(
                f"PseudoOperator[{self.cfg.loop_name}] stopped after {iteration} iteration(s)."
            )
        return 0


__all__ = ["PseudoOperatorEngine"]


if __name__ == "__main__":  # pragma: no cover — debugging convenience
    from .config import load_pseudo_operator_config

    logging.basicConfig(level=logging.INFO)
    sys.exit(PseudoOperatorEngine(load_pseudo_operator_config(Path(sys.argv[1]))).run())
