---
status: implemented
owner: operator
created: 2026-07-06
---

# PseudoOperator — the shared session-loop harness

**Status:** implemented (Track B of the OC/VF grounded audit)
**Spec lineage:** the PlatformManifest architecture spec "OC Audit Findings +
PseudoOperator Spec", §4 (lives in the PlatformManifest repo).

## Why

OperationsCenter and VF each ran a near-copy-paste `tools/loop/controller.py`
(~80% identical by function). Fixes landed in one and not the other (the
Track-A7 lock/timeout/caps hardening reached VF first; OC's copy still had the
TOCTOU lock and no caps). The audit's PseudoOperator finding: the machinery
exists twice, the config schema that should drive it (`CLConfig.LoopConfig`)
was inert, and the guardrails lived in prompt prose.

## Shape

- **Mechanism** (this package, `context_lifecycle.pseudo_operator`): atomic
  hostname-aware locking, bounded session spawn (hard wall timeout), backend
  cooldown/fallback ladder with rate-limit reset parsing + limit-kind
  classification, CL anchoring (session start/end, hydrate/capture for
  non-hook backends), enforced iteration/consecutive-failure caps, adaptive
  delay, pause/stop/signal channels, runtime state file.
- **Policy** (per-repo config, `pseudo_operator:` section): loop name, prompt,
  backend list (priority + models + efforts), caps, delay strategy
  (`fixed` | `schedule_state` | `phase_status`), env file, hook commands.
- **Hooks** (argv lists, shelled out): `pre_iteration` (OC self-update),
  `seed_cooldowns` / `on_cooldown` (OC usage-store bridge), `session_end`.
  Repo-specific *code* stays in the repo; the engine only knows the contract.

Deliberately NOT unified (control_plane_and_anchor.md's over-unification
caution): the repos keep distinct prompts, cadences, and hook scripts — the
shared thing is the harness, not the fleet.

## Config schema (fail-closed)

`PseudoOperatorConfig` uses `extra="forbid"` — a typo'd guardrail fails the
launch instead of silently vanishing (the previous `extra="allow"` schema is
exactly how the loop config stayed inert). Caps are required-positive with
safe defaults; there is no "unlimited" spelling.

Config home per repo: VF `.context/config.yaml`, OC `.console/workers.yaml`
— both gain a `pseudo_operator:` mapping; `cl loop run --config <file>`
consumes it.

## Away/lazy trigger (spec §4.4)

`cl loop pause` writes a pause flag: the engine idles (no sessions) without
exiting until `cl loop resume`. This is the mechanizable operator-present
signal; the stop flag remains the terminal one.

## No-self-rewrite (spec §4.5)

Not enforced here — that is Track C's restorer (deploy-only-from-signed-
reference). This package keeps the config a plain file so the restorer can
own its write path.

## Migration

Each consumer repo replaces its `tools/loop/controller.py` with the config
section + a launcher change (`nohup … cl loop run --config …`); the old file
becomes a thin exec shim during the transition. Runtime state moves to
`<repo>/tools/loop/state/` (fresh paths — no migration of old lock/state
files; stop the old controller before switching).
