# ContextLifecycle

A generic, configurable **cognition lifecycle runtime** for bounded, resumable agent sessions.

Drop it into any repo. Configure your watchers and workers. Your loops stop being immortal cognition sinks.

---

## The Problem

Operational agent loops accumulate:

- runaway context growth
- cache amplification
- subagent recursion costs
- instruction fade-out
- context entropy
- increasing token inefficiency over time

The system starts thinking forever, remembering everything forever, recursively exploring forever.

## The Solution

```text
boot
→ load checkpoint
→ load capsule
→ inspect evidence
→ reason briefly
→ emit actions
→ checkpoint
→ terminate or compact
```

Context is infrastructure. Not merely prompt data.

---

## What This Is

`ContextLifecycle` is a **generic configurable cognition lifecycle runtime** — not a schema dump or a spec-only repository.

It provides:

- **Core schemas** — `InvestigationCapsule`, `LoopCheckpoint`, `WorkerHandoff`, combined `worker_scope`/`lease`
- **`.context/` surface** — per-repo durable cognition state directory
- **ContextGuard** — runtime-neutral lifecycle enforcement engine with adapter-specific implementations
- **Runtime adapters** — Claude Code (included), Codex/Aider/subprocess (extensible)
- **Templates and examples** — ready-to-configure capsules and checkpoints

---

## What This Is Not

- **Not an agent framework.** It does not spawn, schedule, or orchestrate agents — it bounds the cognition lifecycle of loops you already run.
- **Not a prompt library or model wrapper.** Models and runtimes are replaceable; the lifecycle surface is the durable part.
- **Not OC- or consumer-specific scaffolding.** Schemas and enforcement are generic; per-repo watcher/worker policy lives in each consumer's `.context/config.yaml`.
- **Not a persistence/database layer.** State lives as plain YAML under `.context/`; there is no server, daemon, or external store.

---

## Quick Start

```bash
# 1. Add .context/ to your repo
cp -r .context/ /your/repo/.context/

# 2. Configure
cp .context/templates/clp_config.template.yaml /your/repo/.context/config.yaml
# edit config.yaml for your watchers and workers

# 3. Wire ContextGuard (Claude Code)
cp -r adapters/claude/.claude/ /your/repo/.claude/
```

---

## Architecture

ContextLifecycle is a small Python library (`context_lifecycle`) plus a `.context/` cognition surface and runtime adapters. The pieces:

```text
cl session start <manifest>   →  resolves an anchor manifest (via RepoGraph),
                                  mints a session id, exports CL_ANCHOR / CL_SESSION_ID
        │
        ▼
runtime adapter (e.g. Claude Code hooks)
        │   PreToolUse / Stop
        ▼
cl hook pre_tool_use | stop   →  pure decision functions over loaded state
        │
        ▼
.context/sessions/<sid>/       (active capsules, handoffs, checkpoints)
```

Module map (`src/context_lifecycle/`):

| Module | Responsibility |
| ------ | -------------- |
| `models/` | Pydantic schemas — `InvestigationCapsule`, `LoopCheckpoint`, `WorkerHandoff`, `CLConfig` |
| `hooks/` | Pure decision logic (`evaluate_pre_tool_use`, `evaluate_stop`) returning allow/block/warn |
| `session/` | Anchor resolution, session ids, on-disk path layout |
| `cli/` | `cl` Typer entrypoint wrapping sessions and hook adapters |
| `io/` | YAML load/dump helpers |
| `errors.py` | Typed error hierarchy mapped to CLI exit codes |

Decision functions are pure (state in, verdict out); the CLI layer owns I/O and exit-code mapping.

---

## Surface Layout

| Surface     | Purpose                                                           |
| ----------- | ----------------------------------------------------------------- |
| `.console/` | operational truth / dashboards / heartbeats / operator state      |
| `.context/` | runtime-neutral cognition state / capsules / checkpoints / leases |
| `.agent/`   | generic runtime integrations / hooks / adapters                   |
| `.claude/`  | Claude-specific adapter/runtime integration                       |
| `.codex/`   | Codex-specific adapter/runtime integration (future)               |

`.context/` is the durable cognition surface. It is runtime-agnostic. `.claude/` is one adapter implementation.

---

## Design Principles

- **Models are replaceable.** Runtimes are replaceable. Agent frameworks are replaceable. Lifecycle infrastructure is durable.
- **Vocabulary without enforcement is documentation.** ContextGuard enforces the boundary.
- **Loops should not think deeply.** Observe → classify → dispatch → checkpoint → sleep.
- **Workers are disposable.** Load capsule → reason briefly → emit findings → checkpoint → terminate.
- **Capsule = durable resumable artifact.** Scratch = disposable local working state.

---

## Schemas

| Schema                  | Purpose                                           |
| ----------------------- | ------------------------------------------------- |
| `InvestigationCapsule`  | Resumable investigation state — the core primitive |
| `LoopCheckpoint`        | Loop continuity and orchestrator state            |
| `WorkerHandoff`         | Clean worker dispatch without history dumping     |
| `worker_scope` / `lease`| Combined scope and budget for a bounded worker    |

See `.context/schemas/` for full field definitions.

---

## ContextGuard

`ContextGuard` is the enforcement layer. Schemas describe the boundary. ContextGuard enforces it.

- Injects active capsule before worker actions
- Blocks actions when lease is expired or capsule is missing
- Blocks forbidden path mutations
- Requires checkpoint before relaunch
- Maps `context_risk` flags to enforcement actions

See `docs/context_guard.md` for the full design and adapter contract.

Current adapters: **Claude Code** (`adapters/claude/`)

---

## Presets

Ready-to-use configs for common patterns:

| Preset | Use case |
|--------|----------|
| [presets/audit-sitter.yaml](presets/audit-sitter.yaml) | Automated audit loops with gate failure investigation |
| [presets/watchdog-loop.yaml](presets/watchdog-loop.yaml) | Operational watchdog loops monitoring invariants |
| [presets/ci-investigator.yaml](presets/ci-investigator.yaml) | CI failure investigation workers |

---

## Docs

- [docs/adopting.md](docs/adopting.md) — How to adopt CLP in your repo
- [docs/context_guard.md](docs/context_guard.md) — ContextGuard design and adapter contract
- [docs/adapters/adapter_contract.md](docs/adapters/adapter_contract.md) — Full adapter interface spec
- [docs/adapters/claude_code_adapter.md](docs/adapters/claude_code_adapter.md) — Claude Code adapter behavior
- [docs/philosophy.md](docs/philosophy.md) — Design philosophy
- [docs/ecosystem.md](docs/ecosystem.md) — Real-world consumers and future primitives

---

## Real-world Consumers

- [OperationsCenter](https://github.com/ProtocolWarden/OperationsCenter) — watchdog loop integration
- [PrivateConsumer](https://example.com/private-consumer) — audit sitter integration

---

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
