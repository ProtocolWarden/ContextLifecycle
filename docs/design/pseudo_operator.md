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

## Signed config — the live-plane trust anchor (Track C)

Implemented in `signing.py` + the `cl loop sign-config` / `verify-config`
commands, following deploy-only-from-signed-reference:

- The operator signs the `pseudo_operator:` section ONCE, offline
  (`cl loop sign-config --key <ed25519 PEM>` — same key conventions as the OC
  EVAL corpus; the private key never touches a fleet host). This writes
  `pseudo_operator.signed.json` + `.sig` beside the config; the pubkey hex is
  pinned in `operator_pubkey.ed25519` (committed, CODEOWNERS-protected).
- `cl loop run` verifies at launch: **ok** runs the live section (it IS the
  reference); **drift** runs the SIGNED REFERENCE and flags the divergence —
  restore-by-consumption, so the fleet has no write path into its own
  guardrails and "restore vs. change" needs no intent-classifier; **bad
  signature** always refuses; **unsigned** warns loudly (pre-anchoring mode)
  and refuses when the launcher passes `--require-signed`.
- The restorer check is `cl loop verify-config` (exit 0 ok / 3 drift /
  4 bad signature / 5 unsigned) — run it from cron/systemd for continuous
  detection; a legitimate change is a new `sign-config` by the operator.
- Runtime state exposes `signed_status` so the status pane shows the anchor
  posture.

Threat notes: deleting the reference/pubkey locally downgrades to unsigned —
loud, refusable via `--require-signed` at the launcher (outside the config the
agent can reach), and the committed truth is restored by git. Signature
verification with the pinned pubkey is the no-self-rewrite invariant (§4.5).

## Migration

Each consumer repo replaces its `tools/loop/controller.py` with the config
section + a launcher change (`nohup … cl loop run --config …`); the old file
becomes a thin exec shim during the transition. Runtime state moves to
`<repo>/tools/loop/state/` (fresh paths — no migration of old lock/state
files; stop the old controller before switching).
