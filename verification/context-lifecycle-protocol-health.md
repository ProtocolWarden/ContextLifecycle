# Phase A — CLP Runtime Health Inventory

**Repo:** ContextLifecycle  
**Date:** 2026-05-21  
**Branch:** feat/contextguard-hook-fixes  
**Purpose:** Confirm CLP is a working runtime, not just documentation.

---

## 1. Directory Structure

| Path | Present | Note |
|------|---------|------|
| `.context/schemas/` | ✅ | `investigation_capsule.yaml`, `loop_checkpoint.yaml`, `worker_handoff.yaml` |
| `.context/templates/` | ✅ | All four templates present (capsule, checkpoint, handoff, config) |
| `.context/active/` | ✅ | Directory exists (empty = no active capsule, correct at rest) |
| `.context/checkpoints/` | ✅ | Directory exists |
| `.context/handoffs/` | ✅ | Directory exists |
| `.context/examples/` | ✅ | `oc_watchdog_checkpoint.yaml`, `pc_audit_capsule.yaml` |
| `adapters/claude/hooks/pre_tool_use.sh` | ✅ | ContextGuard enforcement hook |
| `adapters/claude/hooks/stop.sh` | ✅ | ContextGuard stop hook |
| `adapters/claude/hooks/README.md` | ✅ | Installation and behavior docs |
| `adapters/claude/settings.json` | ✅ | Claude Code hook registration |
| `presets/` | ✅ | `watchdog-loop.yaml`, `audit-sitter.yaml`, `ci-investigator.yaml` |
| `docs/context_guard.md` | ✅ | Hook behavior reference |
| `docs/adopting.md` | ✅ | Adoption guide |

---

## 2. Schema Files

### investigation_capsule.yaml
```
path: .context/schemas/investigation_capsule.yaml
required_fields: capsule_id, schema_version, status, created_at, title
```

### loop_checkpoint.yaml
```
path: .context/schemas/loop_checkpoint.yaml
required_fields: checkpoint_id, schema_version, created_at, current_phase, orchestrator
```

### worker_handoff.yaml
```
path: .context/schemas/worker_handoff.yaml
required_fields: handoff_id, schema_version, created_at, expires_at, worker_scope, lease
```

---

## 3. Hook Registration

File: `adapters/claude/settings.json`

Expected registration:
- `PreToolUse`: `adapters/claude/hooks/pre_tool_use.sh`
- `Stop`: `adapters/claude/hooks/stop.sh`

Hook exit protocol:
- Exit 0 = allow
- Exit 2 + JSON `{"decision": "block", "reason": "..."}` = block
- stderr = operator-visible warning

---

## 4. Config Template

File: `.context/templates/clp_config.template.yaml`

Key sections:
- `guard.require_capsule` — block tool calls without active capsule
- `guard.enforce_lease` — block on expired handoff lease
- `guard.capsule_path` — location of active capsule
- `loop.checkpoint_on_stop` — require checkpoint at session end

---

## 5. Presets

| File | Use case |
|------|----------|
| `presets/watchdog-loop.yaml` | OC-style monitoring orchestrator |
| `presets/audit-sitter.yaml` | consumer-style audit investigator |
| `presets/ci-investigator.yaml` | CI failure investigation worker |

---

## 6. Hook Enforcement Summary

| Rule | Hook | Behavior |
|------|------|----------|
| require_capsule | pre_tool_use | Block if no active capsule (when enabled) |
| malformed capsule | pre_tool_use | Block if capsule missing required fields or unparseable |
| lease expiry | pre_tool_use | Block if handoff `expires_at` is in the past |
| forbidden_paths | pre_tool_use | Block writes matching forbidden prefix |
| allowed_paths | pre_tool_use | Block writes outside allowed prefix list (when non-empty) |
| mutation_policy: read_only | pre_tool_use | Block all writes |
| max_subagents: 0 | pre_tool_use | Block Agent spawning |
| context_risk.high_parallelism | pre_tool_use | Block Agent spawning |
| context_risk.subagent_heavy | pre_tool_use | Warn on Agent spawn |
| context_risk.checkpoint_stale | pre_tool_use | Block all tool calls |
| context_risk.long_lived_session | pre_tool_use | Warn on all tool calls |
| context_risk.reload_scope_too_large | pre_tool_use | Warn on Read/Bash/Glob |
| checkpoint_on_stop | stop | Warn/surface if no checkpoint written this session |
| active capsule still 'active' | stop | Warn to update status or handoff_notes |

---

## 7. Known Limitations

- Stop hooks cannot hard-block session termination in all Claude Code cases; checkpoint enforcement is surfaced prominently but is advisory
- Hook enforcement requires `python3` and `pyyaml` to be available in the execution environment
- `allowed_paths` matching is prefix-based (string `==` prefix), not glob pattern

---

---

## 8. Portability Fix Applied

**Finding:** `pre_tool_use.sh` used `jq` for JSON parsing but `jq` is not installed on all developer systems. The hook would fail with a non-zero error exit (not a clean block) when `jq` was absent, defeating enforcement.

**Fix:** Added `python3` fallback for all `jq` calls. Hook now prefers `jq` when available, falls back to `python3 -c "import json..."` otherwise. `python3` + `pyyaml` are already required for YAML parsing in the same hook.

**Commit:** `fix(hooks): add python3 fallback for jq; add hook test harness`

---

## 9. Test Harness

`adapters/claude/hooks/tests/run_hook_tests.sh` — 22 fixture test cases covering all enforcement paths.  
`adapters/claude/hooks/tests/validate_examples.py` — schema/example validation for all YAML artifacts.

Run: `bash adapters/claude/hooks/tests/run_hook_tests.sh`  
Run: `python3 adapters/claude/hooks/tests/validate_examples.py`

---

## Status: PASS
