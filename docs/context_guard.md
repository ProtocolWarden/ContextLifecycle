# ContextGuard

The enforcement layer for cognition lifecycle rules.

Schemas describe the boundary. ContextGuard enforces it.

---

## What ContextGuard Is

ContextGuard is a **runtime-neutral lifecycle enforcement system**. It is not Claude-specific. It is not a hook library. It is a policy engine with adapter-specific implementations.

```
ContextGuard core  = runtime-neutral policy engine
runtime adapter    = adapter-specific enforcement layer
```

The Claude Code adapter (in `adapters/claude/`) is the first implementation. Future adapters may target Codex, Aider, subprocess runners, or CI environments.

---

## Responsibilities

| Responsibility                                    | Trigger                                      |
| ------------------------------------------------- | -------------------------------------------- |
| Inject active capsule before worker action        | PreToolUse — any tool call                   |
| Block action if no active capsule (when required) | PreToolUse — `guard.require_capsule: true`   |
| Block action if lease expired                     | PreToolUse — lease `expires_at` check        |
| Block action if worker attempts forbidden path    | PreToolUse — Write/Edit/Bash tool calls      |
| Block subagent spawn if lease disallows it        | PreToolUse — Agent tool calls                |
| Warn or block if context reload is too broad      | PreToolUse — Read/Explore tool calls         |
| Require checkpoint before relaunch                | Stop hook                                    |
| Require capsule update before session termination | Stop hook                                    |

---

## context_risk Enforcement

`context_risk` flags in LoopCheckpoint are not passive metadata. When ContextGuard reads them, it maps them to enforcement actions.

| Risk Flag                      | ContextGuard Action                                     |
| ------------------------------ | ------------------------------------------------------- |
| `long_lived_session: true`     | Force checkpoint + compaction before next tool call     |
| `high_parallelism: true`       | Deny additional worker spawning                         |
| `subagent_heavy: true`         | Block Explore escalation / reduce subagent budget       |
| `checkpoint_stale: true`       | Require checkpoint refresh before dispatch              |
| `reload_scope_too_large: true` | Require warm/cold pruning before reload                 |

Risk flags without enforcement are documentation. Risk flags with ContextGuard reactions are lifecycle policy.

---

## Adapter Contract

Every ContextGuard adapter must implement:

| Hook         | Trigger                          | Required behavior                                |
| ------------ | -------------------------------- | ------------------------------------------------ |
| `pre_action` | Before any tool call             | Load active capsule, check lease, check scope    |
| `pre_write`  | Before Write/Edit/Bash mutations | Validate path against `worker_scope.forbidden_paths` |
| `pre_spawn`  | Before Agent/subagent calls      | Check `lease.max_subagents` and `allowed_subagents` |
| `on_stop`    | On session/worker termination    | Require checkpoint write + capsule update        |

Adapters may implement these as runtime hooks, shell interceptors, or external watchdog checks.

For the full adapter interface specification, see `docs/adapters/adapter_contract.md`.

---

## Adapters

| Adapter        | Status       | Location              |
| -------------- | ------------ | --------------------- |
| Claude Code    | Implemented  | `adapters/claude/`    |
| Codex CLI      | Planned      | `adapters/codex/`     |
| Aider          | Planned      | `adapters/aider/`     |
| subprocess     | Planned      | `adapters/subprocess/`|
| External watchdog | Option B  | See below             |

---

## Option B: External Watchdog

For runtimes that don't support in-process hooks, an external watchdog process can enforce lifecycle rules by inspecting:

- `.context/active/` — active capsule state
- `.context/checkpoints/` — checkpoint freshness
- `.console/` — operational heartbeats
- Process state / PIDs in `.context/tmp/`
- Heartbeat files

The watchdog can enforce:
- Stale leases
- Dead workers
- Missing checkpoints
- Repeated restarts
- Runaway runtime

This is complementary to, not a replacement for, in-process adapter hooks.

---

## What ContextGuard Is Not

- It is not a conversation memory system
- It is not an execution engine
- It is not a replacement for worker leases (leases define the budget; ContextGuard enforces it)
- It is not self-discipline (self-discipline is not enforcement)

```
Vocabulary without enforcement becomes another docs folder.
```
