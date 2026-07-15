# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""PseudoOperatorConfig — the activated loop config schema (Track B).

This is the schema that makes the previously-inert ``loop:`` sections of
VF ``.context/config.yaml`` / OC ``.console/workers.yaml`` load-bearing: the
engine consumes every field here, and unknown keys are REJECTED (a typo'd
guardrail must fail the launch, not silently vanish into ``extra="allow"``).

Design rule (control_plane_and_anchor.md): the engine is the shared
*mechanism*; everything repo-specific enters as declarative *policy* — either
data (delay maps, models, caps) or hook commands (argv lists the engine
shells out to, so e.g. OC's usage-store bridge lives in OC, not here).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BackendSpec(BaseModel):
    """One agent backend in priority order."""

    model_config = ConfigDict(extra="forbid")

    name: str  # "claude" | "opus" | "codex" — selects the CLI invocation shape
    kind: str = ""  # invocation shape; defaults to name ("claude"/"codex")
    model: str
    effort: str = "medium"

    @field_validator("name")
    @classmethod
    def _known_name(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("backend name must not be empty")
        return v

    @property
    def invocation_kind(self) -> str:
        kind = self.kind or ("codex" if self.name == "codex" else "claude")
        if kind not in {"claude", "codex"}:
            raise ValueError(f"unknown backend invocation kind {kind!r}")
        return kind


class DelayPolicy(BaseModel):
    """Adaptive inter-session delay.

    ``kind``:
      - ``fixed`` — always ``default_delay_seconds``.
      - ``schedule_state`` — the previous session may write a schedule file
        with a health-state string; ``state_delays`` maps state → delay
        (the OC watchdog policy).
      - ``phase_status`` — read the newest ``<status_glob>`` dir's
        ``run_status.json`` ``phase`` field; ``phase_delays`` maps
        phase → delay (the VF audit-monitor policy). Also enables early
        wake when the run reaches a terminal status.

    A session-written schedule file (``delay_s`` override) always wins,
    whatever the kind — that is the loop's own steering channel.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = "fixed"
    default_delay_seconds: int = 600
    state_delays: dict[str, int] = Field(default_factory=dict)
    phase_delays: dict[str, int] = Field(default_factory=dict)
    # Glob (relative to repo_root) whose newest matching dir holds
    # run_status.json — phase_status kind only.
    status_glob: str = ""

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        if v not in {"fixed", "schedule_state", "phase_status"}:
            raise ValueError(f"unknown delay policy kind {v!r}")
        return v


class HookCommands(BaseModel):
    """Repo-specific extension points, as argv lists the engine shells out to.

    All hooks are best-effort (a failing hook logs and continues) EXCEPT
    where noted. Hooks run in repo_root with the engine's environment.

      - ``pre_iteration``: run at the top of every iteration (OC uses this
        for its self-update: git pull + watcher restart).
      - ``seed_cooldowns``: run once at startup; stdout must be a JSON object
        ``{backend_name: iso8601_or_null}`` merged into the cooldown table
        (OC seeds from its usage store).
      - ``on_cooldown``: run when a backend enters cooldown, with one JSON
        argument ``{"backend":…, "reset_at":…, "limit_kind":…, "model":…}``
        (OC bridges this into its usage store).
      - ``session_end``: run after every session with one JSON argument
        ``{"iteration":…, "backend":…, "returncode":…, "log_path":…}``.
      - ``budget_guard``: run at the top of every iteration, before backend
        selection; stdout must be a JSON object ``{backend_name:
        iso8601_or_null}``. Non-null future times EXTEND that backend's
        cooldown — never shorten one (a budget horizon must not mask a real
        limit reset). OC uses this to divert to codex when the shared
        subscription budget reserve is reached.
    """

    model_config = ConfigDict(extra="forbid")

    pre_iteration: list[str] = Field(default_factory=list)
    seed_cooldowns: list[str] = Field(default_factory=list)
    on_cooldown: list[str] = Field(default_factory=list)
    session_end: list[str] = Field(default_factory=list)
    budget_guard: list[str] = Field(default_factory=list)


class PseudoOperatorConfig(BaseModel):
    """The full engine policy for one repo's loop."""

    model_config = ConfigDict(extra="forbid")

    # Identity
    loop_name: str  # short tag, e.g. "oc" / "vf" — CL lineage + log prefix
    repo_root: Path
    session_prompt_file: Path  # relative to repo_root unless absolute

    # Backends, priority order. Default mirrors the two live controllers.
    backends: list[BackendSpec] = Field(
        default_factory=lambda: [
            BackendSpec(name="claude", model="claude-sonnet-5", effort="medium"),
            BackendSpec(name="opus", model="claude-opus-4-8", effort="medium"),
            BackendSpec(name="codex", kind="codex", model="gpt-5.4", effort="medium"),
        ]
    )

    # Enforced guardrails (Track A7 made these code-side; here they are config
    # with safe defaults — a MISSING field never means "unlimited").
    session_timeout_seconds: int = 2700
    max_iterations: int = 200
    max_consecutive_failures: int = 5

    delay: DelayPolicy = Field(default_factory=DelayPolicy)
    hooks: HookCommands = Field(default_factory=HookCommands)

    # Optional env file sourced (KEY=VALUE lines) into every session env —
    # the OC fleet's .env.operations-center.local.
    env_file: Path | None = None

    # State/coordination paths, relative to repo_root unless absolute.
    state_dir: Path = Path("tools/loop/state")
    log_file: Path | None = None  # None = stdout only (wrapper redirects)

    @field_validator("session_timeout_seconds", "max_iterations", "max_consecutive_failures")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("guardrail caps must be positive")
        return v

    # ── resolved paths ────────────────────────────────────────────────────
    def _resolve(self, p: Path) -> Path:
        return p if p.is_absolute() else self.repo_root / p

    @property
    def prompt_path(self) -> Path:
        return self._resolve(self.session_prompt_file)

    @property
    def state_path(self) -> Path:
        return self._resolve(self.state_dir)

    @property
    def lock_path(self) -> Path:
        return self.state_path / "controller.lock"

    @property
    def runtime_state_path(self) -> Path:
        return self.state_path / "controller_state.json"

    @property
    def stop_flag_path(self) -> Path:
        return self.state_path / "stop.flag"

    @property
    def pause_flag_path(self) -> Path:
        return self.state_path / "pause.flag"

    @property
    def signal_path(self) -> Path:
        return self.state_path / "signal.json"

    @property
    def schedule_path(self) -> Path:
        return self.state_path / "schedule.json"

    @property
    def session_log_dir(self) -> Path:
        return self.state_path / "sessions"

    @property
    def resolved_log_file(self) -> Path | None:
        return self._resolve(self.log_file) if self.log_file else None

    @property
    def resolved_env_file(self) -> Path | None:
        return self._resolve(self.env_file) if self.env_file else None


def load_pseudo_operator_config(config_file: Path) -> PseudoOperatorConfig:
    """Load the ``pseudo_operator:`` section of a repo config YAML.

    Accepts either a file whose top level IS the PseudoOperatorConfig mapping,
    or a repo config (VF ``.context/config.yaml`` / OC ``.console/workers.yaml``)
    with a ``pseudo_operator:`` key. ``repo_root`` defaults to the config
    file's repo (the directory containing ``.context``/``.console``, else the
    file's parent).

    Fail-closed: a missing section or an invalid field raises — the loop must
    not start on a config it cannot fully honor.
    """
    from context_lifecycle.io.yaml_io import load_yaml_safe

    data = load_yaml_safe(config_file, default=None)
    if not isinstance(data, dict):
        raise ValueError(f"{config_file}: not a YAML mapping")
    section = data.get("pseudo_operator", data)
    if not isinstance(section, dict) or "loop_name" not in section:
        raise ValueError(
            f"{config_file}: no pseudo_operator section (or missing loop_name) — "
            "the loop refuses to start on an unconfigured repo"
        )
    section = dict(section)
    if "repo_root" not in section:
        parent = config_file.resolve().parent
        section["repo_root"] = str(parent.parent if parent.name in {".context", ".console"} else parent)
    return PseudoOperatorConfig.model_validate(section)


def _with_repo_root(section: dict, config_file: Path) -> dict:
    """Inject the default ``repo_root`` into a section loaded from anchor bytes."""
    section = dict(section)
    if "repo_root" not in section:
        parent = config_file.resolve().parent
        section["repo_root"] = str(
            parent.parent if parent.name in {".context", ".console"} else parent
        )
    return section


def load_verified_config(
    config_file: Path, *, require_signed: bool = False, require_committed: bool = False
) -> tuple[PseudoOperatorConfig, str, str]:
    """Load the loop config through the trust anchor (Track C) or, when it is
    not anchored, the keyless committed-truth check (C2).

    Returns ``(config, signed_status, detail)`` where signed_status is
    ok | drift | unsigned | drift_unsigned. Enforcement:

    - ``ok``: run the live section (it IS the signed reference).
    - ``drift``: run the SIGNED REFERENCE, not the live section — restore-by-
      consumption; the caller logs the divergence loudly.
    - ``bad_signature``: raise — a present-but-unverifiable reference means the
      anchor is compromised or corrupt; never fall back to the live section.
    - no signed reference (``unsigned``): raise when ``require_signed``;
      otherwise fall through to the committed-truth check (C2, below).

    C2 — when no signed reference exists (Track C wins unchanged whenever one is
    present, so this runs only in the unsigned case):

    - committed match: run the live section (it IS the committed truth) as
      normal unsigned.
    - ``drift_unsigned``: the live section diverges from ``origin/main`` — run
      the COMMITTED copy (restore-by-consumption, no YAML rewriting) and flag it
      loudly, OR raise when ``require_committed``.
    - skip (offline / fetch failure / config absent on origin): run the live
      section unsigned with a loud note — degrade-never-halt for ordinary
      launches; but raise when ``require_committed`` — the gate cannot confirm
      the live section matches committed truth, and a fail-open skip would let
      anyone bypass ``--require-committed`` by cutting network access (parity
      with ``--require-signed``, which likewise refuses when it cannot confirm).
    """
    from .committed import verify_committed
    from .signing import verify_reference

    result = verify_reference(config_file)
    if result.status == "bad_signature":
        raise ValueError(f"signed loop config FAILED verification: {result.detail}")
    if result.status == "drift":
        section = _with_repo_root(result.reference or {}, config_file)
        return PseudoOperatorConfig.model_validate(section), "drift", result.detail
    if result.status == "ok":
        return load_pseudo_operator_config(config_file), "ok", result.detail

    # No signed reference — Track C does not apply.
    if require_signed:
        raise ValueError(
            f"--require-signed set but the loop config is not anchored ({result.detail}); "
            "sign it with `cl loop sign-config`"
        )

    # C2: keyless committed-truth check against origin/main.
    committed = verify_committed(config_file)
    if committed.status == "drift":
        if require_committed:
            raise ValueError(
                "--require-committed set but the live loop config DIVERGES from "
                f"origin/main ({committed.detail}); commit the change on origin/main "
                "or relaunch without --require-committed"
            )
        section = _with_repo_root(committed.committed or {}, config_file)
        return PseudoOperatorConfig.model_validate(section), "drift_unsigned", committed.detail
    if committed.status == "skip":
        if require_committed:
            raise ValueError(
                "--require-committed set but the live loop config could not be "
                f"verified against origin/main ({committed.detail}); the gate "
                "cannot confirm committed truth — resolve connectivity or "
                "relaunch without --require-committed"
            )
        return (
            load_pseudo_operator_config(config_file),
            "unsigned",
            f"{result.detail}; committed-truth check skipped: {committed.detail}",
        )
    # match — live section is the committed truth.
    return load_pseudo_operator_config(config_file), "unsigned", committed.detail


__all__ = [
    "BackendSpec",
    "DelayPolicy",
    "HookCommands",
    "PseudoOperatorConfig",
    "load_pseudo_operator_config",
    "load_verified_config",
]
