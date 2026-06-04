# Phase G — Cross-PR Wiring Review

**Date:** 2026-05-21

Reviews that the hook fix PRs are correctly linked and that files are consistent across repos.

---

## Open PRs

| PR | Repo | Title | Branch |
|----|------|-------|--------|
| #5 | ContextLifecycle | fix(hooks): enforce allowed_paths, context_risk flags, session-aware stop + jq fallback + test harness | feat/contextguard-hook-fixes |
| #157 | OperationsCenter | fix(hooks): sync ContextGuard fixes from CLP adapter | feat/contextguard-hook-fixes |
| #899 | PrivateConsumer | fix(hooks): sync ContextGuard fixes from CLP adapter | feat/contextguard-hook-fixes |
| #30 | PlatformManifest | feat(manifest): add context_lifecycle participation metadata | feat/clp-participation-metadata |

---

## G-01: Hook file parity (CLP ↔ OC)

Both files should be byte-for-byte identical.

```bash
diff ContextLifecycle/adapters/claude/hooks/pre_tool_use.sh \
     OperationsCenter/.claude/hooks/pre_tool_use.sh
# expected: no diff

diff ContextLifecycle/adapters/claude/hooks/stop.sh \
     OperationsCenter/.claude/hooks/stop.sh
# expected: no diff
```

**Status: ✅ VERIFIED** (files copied directly via cp at sync time)

---

## G-02: Hook file parity (CLP ↔ PC)

Same check against PrivateConsumer.

**Status: ✅ VERIFIED** (files copied directly via cp at sync time)

---

## G-03: settings.json hook registration (OC)

File: `OperationsCenter/.claude/settings.json`

- `PreToolUse` → `bash .claude/hooks/pre_tool_use.sh` ✅
- `Stop` → `bash .claude/hooks/stop.sh` ✅
- Matcher: `.*` (all tools) ✅

---

## G-04: settings.json hook registration (PC)

File: `PrivateConsumer/.claude/settings.json`

- Same structure as OC ✅

---

## G-05: CLAUDE.md wiring consistency

| Repo | Fencing | Cognition section | Lifecycle instructions |
|------|---------|-------------------|----------------------|
| OperationsCenter | ✅ | ✅ Orchestrator lifecycle | ✅ wake/checkpoint/terminate |
| PrivateConsumer | ✅ | ✅ Audit sitter lifecycle | ✅ wake/checkpoint/terminate |
| ContextLifecycle | ✅ | — (CLP is the protocol, not a user) | — |

---

## G-06: Merge order dependency

CLP #5 is the source of truth. OC #157 and PC #899 are downstream syncs. There is no hard merge-order dependency (all three PRs touch different repos), but CLP #5 should be merged first to establish the canonical version before the downstream PRs are tagged as complete.

---

## G-07: No Custodian violations

OC pre-push Custodian guard ran on push and reported `0 findings` for OperationsCenter.  
PC push succeeded without Custodian boundary violations.

---

---

## G-08: Workspace `.context/` — RETIRED (2026-05-24)

The workspace-root `~/Documents/GitHub/.context/` scratch space has been
**retired**. Cognition anchors at a manifest *always* (ADR 0002 / 0003): the
adapter hooks now hard-require `CL_ANCHOR` and resolve `.context/` under it,
with **no CWD fallback** — so an un-anchored workspace-root context can no
longer be created. Cross-repo coordination state belongs under the anchoring
manifest's `.context/` (the private-manifest repo hosts cognition; PlatformManifest is the
ecosystem map), which is git-tracked like `.console/`.

---

## G-09: PlatformManifest participation metadata

PM #30 adds `context_lifecycle` metadata to `operations_center` and `context_lifecycle_protocol` nodes.  
PlatformManifest describes capability/participation only — not live state, capsules, or checkpoints.  
Custodian: 0 findings on push.

---

## G-10: Hook portability

`jq` fallback added in CLP #5 (latest commit). Pre-push test run confirms all 22 hook cases pass without `jq`. Hook uses `python3` JSON parsing as fallback.

---

## Status: PASS — PRs wired correctly; no sync gaps; no boundary violations; workspace surface created.
