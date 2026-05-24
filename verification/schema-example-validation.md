# Phase B — Schema Example Validation

**Date:** 2026-05-21  
**Validator:** `adapters/claude/hooks/tests/validate_examples.py`

**Actual run output (2026-05-21):**
```
Results: 9 passed, 0 failed
ALL PASS
```

Covers: capsule example, capsule template, checkpoint example, checkpoint template, handoff template, config template, watchdog-loop preset, audit-sitter preset, ci-investigator preset.

---

## 1. InvestigationCapsule — pc_audit_capsule.yaml

**File:** `.context/examples/pc_audit_capsule.yaml`  
**Schema:** `.context/schemas/investigation_capsule.yaml`

| Required field | Present | Value |
|----------------|---------|-------|
| `capsule_id` | ✅ | `inv-20260521-transcode-quality-001` |
| `schema_version` | ✅ | `0.1` |
| `status` | ✅ | `active` |
| `created_at` | ✅ | `2026-05-21T14:32:00Z` |
| `created_by` | ✅ | `audit-sitter-worker-03` |
| `related_checkpoint_id` | ✅ | `chk-20260521-1430-pc-audit` |
| `current_blocker` | ✅ | Non-empty |
| `current_phase` | ✅ | `remediation` |
| `failing_invariant` | ✅ | Non-empty |

**Optional fields present:** `active_hypotheses`, `evidence_paths`, `recent_failures`, `attempted_remediations`, `next_actions`, `safety_constraints`, `known_safe_boundaries`, `exclusions`, `handoff_notes`

**Parseable YAML:** ✅ (well-formed, no anchors or special constructs)

**Status: PASS**

---

## 2. LoopCheckpoint — oc_watchdog_checkpoint.yaml

**File:** `.context/examples/oc_watchdog_checkpoint.yaml`  
**Schema:** `.context/schemas/loop_checkpoint.yaml`

| Required field | Present | Value |
|----------------|---------|-------|
| `checkpoint_id` | ✅ | `chk-20260521-1600-oc-watchdog` |
| `schema_version` | ✅ | `0.1` |
| `created_at` | ✅ | `2026-05-21T16:00:00Z` |
| `current_phase` | ✅ | `investigating` |
| `orchestrator` | ✅ | Object with `context_risk`, `active_capsule_ids`, etc. |

**orchestrator.context_risk fields:**
| Flag | Value |
|------|-------|
| `long_lived_session` | `false` |
| `high_parallelism` | `false` |
| `subagent_heavy` | `false` |
| `checkpoint_stale` | `false` |
| `reload_scope_too_large` | `false` |

**Optional fields present:** `parent_checkpoint_id`, `active_capsule_ids`, `orchestrator_cycle_id`, `current_operational_state`, `latest_worker_outputs`, `unresolved_blockers`, `next_scheduled_action`, `relaunch_metadata`, `compaction_status`, `operator_summary`

**Parseable YAML:** ✅

**Status: PASS**

---

## 3. WorkerHandoff — template validation

**File:** `.context/templates/worker_handoff.template.yaml`  
**Note:** Template fields are empty strings by design; this validates structure only.

| Required field | Present |
|----------------|---------|
| `handoff_id` | ✅ |
| `schema_version` | ✅ |
| `created_at` | ✅ |
| `source_checkpoint_id` | ✅ |
| `source_capsule_id` | ✅ |
| `target_worker_id` | ✅ |
| `task_description` | ✅ |
| `input_capsule_id` | ✅ |
| `expected_output` | ✅ |
| `completion_criteria` | ✅ |
| `worker_scope.repo` | ✅ |
| `worker_scope.allowed_paths` | ✅ |
| `lease.max_minutes` | ✅ |
| `lease.max_tool_calls` | ✅ |
| `lease.max_subagents` | ✅ |
| `lease.expires_at` | ✅ |

**Parseable YAML:** ✅

**Status: PASS**

---

## 4. Config Template — clp_config.template.yaml

**File:** `.context/templates/clp_config.template.yaml`

Required sections present:
- `guard` (require_capsule, enforce_lease, capsule_path, checkpoint_path, handoff_path)
- `loop` (checkpoint_on_stop)
- `workers` (array with scope and lease fields)

**Parseable YAML:** ✅

**Status: PASS**

---

## 5. ContextGuard malformed-capsule detection (integration with Phase C)

The hook validates these fields on any active capsule before allowing tool calls (when `require_capsule: true`):
- `capsule_id` — non-empty string
- `schema_version` — non-empty string
- `status` — non-empty string (values: `active`, `blocked`, `resolved`, `archived`, `superseded`, `abandoned`)

A capsule file that is valid YAML but missing these fields → `block` with `missing:field1,field2`.  
A capsule file that is not valid YAML → `block` with `malformed:<exception>`.

**Status: PASS**

---

## Overall: PASS
