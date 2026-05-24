# Phase I — Post-Merge Wiring Verification

**Date:** 2026-05-21  
**Status:** Pending merge of CLP #5, OC #157, PC #899

---

## I-01: Hook path resolution (post-merge)

After merge to main, confirm hooks exist at expected paths:

| Repo | Path | Expected |
|------|------|----------|
| ContextLifecycle | `adapters/claude/hooks/pre_tool_use.sh` | exists, executable |
| ContextLifecycle | `adapters/claude/hooks/stop.sh` | exists, executable |
| OperationsCenter | `.claude/hooks/pre_tool_use.sh` | exists, executable |
| OperationsCenter | `.claude/hooks/stop.sh` | exists, executable |
| PrivateConsumer | `.claude/hooks/pre_tool_use.sh` | exists, executable |
| PrivateConsumer | `.claude/hooks/stop.sh` | exists, executable |

```bash
# Verify executable bit
ls -la .claude/hooks/
# Should show -rwxr-xr-x for both files
```

---

## I-02: docs link verification

Links in CLP docs that reference hook files:

| Doc | Link | Points to |
|-----|------|-----------|
| `docs/context_guard.md` | hook path references | `adapters/claude/hooks/` |
| `docs/adopting.md` | installation instructions | `adapters/claude/` |
| `README.md` | hook registration reference | `adapters/claude/settings.json` |

Confirm all links resolve against `main` branch after merge.

---

## I-03: Verification document commit

Verification documents in `verification/` should be committed on the same feature branch or a follow-up. Documents written:

| File | Phase |
|------|-------|
| `context-lifecycle-protocol-health.md` | A (CLP) |
| `operations-center-context-wiring.md` | A (OC) |
| `private-consumer-context-wiring.md` | A (PC) |
| `schema-example-validation.md` | B |
| `contextguard-hook-matrix.md` | C |
| `context-risk-enforcement.md` | D |
| `runtime-presets.md` | E |
| `end-to-end-dry-runs.md` | F |
| `cross-pr-wiring-review.md` | G |
| `merge-readiness.md` | H |
| `post-merge-wiring.md` | I (this file) |

---

## I-04: Tag consideration

After CLP #5 merges and verification passes, consider tagging:

```
context-lifecycle-v0.1.0
```

Tag message:
> ContextGuard hook enforcement complete. Enforces: capsule presence/validity, lease expiry, path scope (allowed + forbidden), mutation policy, subagent budget, context_risk flags (checkpoint_stale block, high_parallelism block, subagent_heavy warn, long_lived_session warn, reload_scope_too_large warn). Session-aware stop hook. Deployed to OC and PC.

---

## I-05: Future work (not blocking)

- Fixture-based automated tests for hook enforcement (`adapters/claude/hooks/tests/`)
- GitHub Actions workflow to run hook tests on PR
- `context_risk` flag setter utility (helper script to update a checkpoint's risk flags without full YAML rewrite)
- Hook parity test: CI step that diffs CLP adapter against OC/PC copies and fails if they diverge

---

## Status: PENDING MERGE
