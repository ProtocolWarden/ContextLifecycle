# Phase E — Runtime Presets Verification

**Date:** 2026-05-21  
**Presets dir:** `presets/`

---

## E-01: watchdog-loop.yaml

**File:** `presets/watchdog-loop.yaml`  
**Target use case:** OC-style operational watchdog monitoring invariants

| Field | Value | Valid |
|-------|-------|-------|
| `guard.enforce_lease` | `true` | ✅ |
| `guard.require_capsule` | `false` | ✅ — watchdog can run a monitoring cycle without an active investigation capsule |
| `loop.checkpoint_on_stop` | `true` | ✅ |
| `loop.max_cycle_minutes` | `60` | ✅ |
| Worker `max_minutes` | `30` | ✅ — half cycle max |
| Worker `max_tool_calls` | `75` | ✅ |
| Worker `allowed_subagents` | `1` | ✅ |
| Worker `allowed_paths` | `.context/`, `.console/`, `src/`, `tests/` | ✅ |
| Worker `forbidden_paths` | `.console/tmp/`, `.context/tmp/` | ✅ |
| Worker `mutation_policy` | `write_allowed` | ✅ |

**Fields requiring repo-specific fill-in:**
- `repo` — must be set to repo name
- `loop.relaunch_command` — must be set to the watchdog script path

**Parseable YAML:** ✅  
**Status: PASS**

---

## E-02: audit-sitter.yaml

**File:** `presets/audit-sitter.yaml`  
**Target use case:** consumer-style automated audit loop for gate failure investigation

| Field | Value | Valid |
|-------|-------|-------|
| `guard.enforce_lease` | `true` | ✅ |
| `guard.require_capsule` | `false` | ✅ |
| `loop.checkpoint_on_stop` | `true` | ✅ |
| Two workers defined | `gate-investigation-worker`, `remediation-worker` | ✅ |
| `remediation-worker.allowed_subagents` | `0` | ✅ — remediation worker cannot spawn sub-workers |
| `remediation-worker.allowed_paths` | `.context/`, `.console/`, `src/` (no `tests/`) | ✅ — narrower than investigation |
| `remediation-worker.max_minutes` | `20` | ✅ — shorter budget than investigation |
| `remediation-worker.max_tool_calls` | `50` | ✅ |

**Fields requiring repo-specific fill-in:**
- `repo`

**Parseable YAML:** ✅  
**Status: PASS**

---

## E-03: ci-investigator.yaml

**File:** `presets/ci-investigator.yaml`

| Attribute | Note |
|-----------|------|
| Purpose | CI failure investigation worker |
| Parseable | ✅ |

**Status: PASS**

---

## E-04: Preset → Config adoption path

1. Copy preset to `.context/config.yaml`
2. Set `repo:` field
3. Set `loop.relaunch_command:` if applicable
4. Adjust `allowed_paths` if repo structure differs from defaults
5. ContextGuard reads this config on every hook invocation — no reload required

---

## E-05: Config field coverage

All fields read by hooks are present in at least one preset:

| Config field | Present in preset | Read by hook |
|--------------|-------------------|--------------|
| `guard.require_capsule` | ✅ | pre_tool_use |
| `guard.enforce_lease` | ✅ | pre_tool_use |
| `guard.capsule_path` | ✅ | pre_tool_use, stop |
| `guard.checkpoint_path` | ✅ | pre_tool_use, stop |
| `guard.handoff_path` | ✅ | pre_tool_use |
| `loop.checkpoint_on_stop` | ✅ | stop |

Worker scope fields in config are advisory (used by orchestrator to construct handoffs); ContextGuard reads scope from the handoff file directly, not from config.

**Status: PASS**

---

## Overall: PASS
