# Ecosystem Expansion

ContextLifecycle is designed to grow. The core primitives (InvestigationCapsule, LoopCheckpoint, WorkerHandoff, ContextGuard) are stable. The ecosystem expands through additional adapters, worker templates, lifecycle policies, and deployment examples.

---

## Real-world Consumers

| Repo | Integration | Notes |
|------|-------------|-------|
| [OperationsCenter](https://github.com/ProtocolWarden/OperationsCenter) | Watchdog loop checkpointing, investigation worker dispatch | Phase 3 |
| [PrivateConsumer](https://example.com/private-consumer) | Audit sitter capsules, gate remediation handoffs | Phase 4 |

---

## Planned Adapters

### Codex CLI Adapter (`adapters/codex/`)

Implements ContextGuard using Codex CLI's tool interception layer. Same behavioral contract as the Claude Code adapter.

### Aider Adapter (`adapters/aider/`)

Implements ContextGuard using Aider's pre/post-command hooks. Useful for repos using Aider as the primary agent runtime.

### Subprocess Adapter (`adapters/subprocess/`)

A shell-based wrapper for non-AI agent workers (bash scripts, Python workers, CI jobs). Reads capsule and lease from `.context/` before launching a subprocess and enforces timeout via the lease `max_minutes` field.

### External Watchdog (`adapters/watchdog/`)

A standalone process that monitors `.context/active/` and `.context/checkpoints/` for stale leases, dead workers, and missing checkpoints. Useful when the primary runtime doesn't support in-process hooks.

---

## Potential Use Cases

### CI/CD Watchdog Loops

```yaml
# .context/config.yaml
workers:
  - id: "ci-failure-investigator"
    task: "investigate CI failure and emit remediation capsule"
    max_minutes: 20
    allowed_paths: [".context/", "ci/", "scripts/"]
    forbidden_paths: [".context/tmp/"]

watchers:
  - id: "ci-watcher"
    watch_path: "ci/results/"
    trigger_on: "failure"
    dispatch_worker: "ci-failure-investigator"
```

### Repo Maintenance Loops

Regular maintenance workers that:
- run dependency audits
- emit investigation capsules for stale dependencies
- checkpoint between cycles
- terminate after each maintenance pass

### Autonomous Audit Loops

Audit workers that:
- load the last audit checkpoint
- run scoped audits against new commits
- emit findings capsules
- hand off to remediation workers for deterministic fixes
- archive capsules when resolved

### Long-running Investigation Workers

Research workers with explicit leases:
- bounded by `max_tool_calls` and `max_minutes`
- required to emit a capsule before termination
- ContextGuard blocks continuation past lease expiry

### Infrastructure Watchdogs

System health monitors that:
- emit LoopCheckpoints per cycle
- dispatch investigation workers on anomaly
- track `context_risk.high_parallelism` when many workers are active

### Coding Agent Orchestration Runtimes

Multi-agent orchestrators that use WorkerHandoff to:
- scope each agent to specific paths and tools
- enforce subagent budgets
- require checkpoint before handing off to the next agent in a chain

---

## Future Primitives

These are not implemented yet. They represent the natural next layer once the core primitives are proven by real usage.

| Primitive | Purpose |
|-----------|---------|
| `CognitiveScheduler` | Schedule bounded worker dispatch based on capsule state and time |
| `ContextCompactor` | Automated context summarization before lease expiry |
| `WorkerLifecycleManager` | Track and enforce worker state machine (dispatched → active → terminated) |
| `EvidenceProjection` | Load only the hot/warm evidence relevant to the current capsule phase |
| `ReasoningBoundary` | Hard token budget enforcement integrated with the lease |
| `CognitiveBudgetPolicy` | Policy objects that define budget rules per worker type |
| `ResumableInvestigationRuntime` | Full runtime for boot → load → reason → checkpoint → terminate |
| `SessionGarbageCollector` | Clean up orphaned leases, stale active capsules, dead handoffs |
| `ContextTemperatureManager` | Automatically classify and prune hot/warm/cold/frozen context |
| `ContextGuard (mature)` | Full runtime-neutral policy engine beyond the initial hook-based v0 |

---

## Contributing New Adapters

See [CONTRIBUTING.md](../CONTRIBUTING.md) and [docs/adapters/adapter_contract.md](adapters/adapter_contract.md).

The adapter contract is stable. Any compliant implementation of the four hooks (pre_action, pre_write, pre_spawn, on_stop) qualifies as a ContextGuard adapter.
