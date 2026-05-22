# Adopting ContextLifecycle

How to add bounded, resumable cognition lifecycle to any repo.

---

## Minimum viable setup (5 minutes)

### 1. Copy `.context/`

```bash
cp -r path/to/ContextLifecycle/.context/ /your/repo/.context/
```

### 2. Configure

```bash
cp /your/repo/.context/templates/clp_config.template.yaml /your/repo/.context/config.yaml
```

Edit `.context/config.yaml` — set `repo:`, configure your workers and watchers.

### 3. Add to `.gitignore`

```
.context/tmp/
.context/active/*.lock
.context/leases/*.live
.context/leases/*.pid
```

### 4. Wire ContextGuard (Claude Code)

```bash
mkdir -p .claude/hooks
cp path/to/ContextLifecycle/adapters/claude/hooks/*.sh .claude/hooks/
chmod +x .claude/hooks/*.sh
```

Merge the hooks block from `adapters/claude/settings.json` into your `.claude/settings.json`.

---

## First checkpoint

At the end of your first session:

```bash
cp .context/templates/loop_checkpoint.template.yaml \
   .context/checkpoints/chk-$(date +%Y%m%d-%H%M)-first.yaml
```

Fill in `checkpoint_id`, `created_at`, `current_operational_state`, and `operator_summary`. Commit it.

---

## First investigation capsule

When you hit a blocker that needs a bounded investigation:

```bash
cp .context/templates/investigation_capsule.template.yaml \
   .context/active/inv-$(date +%Y%m%d)-<slug>.yaml
```

Fill in the required fields. Commit it. The next session loads it cold.

---

## Telling your workers about the lifecycle

Add to your `CLAUDE.md` (or equivalent):

```markdown
## Cognition Lifecycle

On session start: check `.context/active/` for active capsules.
                  check `.context/checkpoints/` for latest checkpoint.
On session end:   write a LoopCheckpoint.
                  update active capsule handoff_notes and next_actions.
Templates: `.context/templates/`
Config:    `.context/config.yaml`
```

---

## What the ContextGuard hooks enforce

After wiring `.claude/hooks/`:

- Sessions without an active capsule will be warned (or blocked if `require_capsule: true`)
- Tool calls against forbidden paths will be blocked
- Subagent spawning past the lease budget will be blocked
- Sessions ending without a checkpoint will trigger a warning
- `context_risk` flags in checkpoints will trigger enforcement actions

---

## Choosing your enforcement level

Start permissive, tighten over time:

| Setting | Value | Effect |
|---------|-------|--------|
| `guard.require_capsule` | `false` | Warn only — recommended for adoption |
| `guard.require_capsule` | `true` | Block all tool calls without an active capsule |
| `guard.enforce_lease` | `true` | Block actions after lease expires |
| `loop.checkpoint_on_stop` | `true` | Warn on session end without checkpoint |

---

## Schema reference

- [InvestigationCapsule](.context/schemas/investigation_capsule.yaml)
- [LoopCheckpoint](.context/schemas/loop_checkpoint.yaml)
- [WorkerHandoff](.context/schemas/worker_handoff.yaml)

---

## Getting help

Open an issue: [github.com/ProtocolWarden/ContextLifecycle/issues](https://github.com/ProtocolWarden/ContextLifecycle/issues)
