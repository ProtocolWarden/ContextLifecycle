# .context/

Runtime-neutral durable cognition surface.

This directory holds resumable investigation state, loop continuity checkpoints, worker handoff records, and lease definitions. It is the persistent memory layer that lets agent loops survive without maintaining immortal conversation sessions.

---

## Directory Layout

| Path            | Purpose                                              | Git policy        |
| --------------- | ---------------------------------------------------- | ----------------- |
| `schemas/`      | Schema definitions for all lifecycle artifacts       | committed         |
| `templates/`    | Starter templates — copy and fill in                 | committed         |
| `examples/`     | Anonymized reference examples                        | committed         |
| `active/`       | Currently active investigation capsules              | committed (durable) |
| `archive/`      | Resolved or superseded capsules                      | committed         |
| `checkpoints/`  | Loop checkpoints — one per orchestrator cycle        | committed         |
| `capsules/`     | Finalized capsules after investigation completes     | committed         |
| `leases/`       | Worker lease definitions                             | committed         |
| `handoffs/`     | Worker handoff records                               | committed         |
| `tmp/`          | Scratch state — process IDs, lock files, raw logs   | gitignored        |

---

## Core Rule

```
capsule    = durable resumable artifact → commit it
scratch    = disposable local working state → gitignore it
```

An active investigation capsule should be committed even if incomplete. Loss of `.context/active/` on crash, reset, or machine switch means loss of investigation state.

---

## Schemas

- `schemas/investigation_capsule.yaml` — resumable investigation state
- `schemas/loop_checkpoint.yaml` — orchestrator loop continuity
- `schemas/worker_handoff.yaml` — clean worker dispatch

All durable artifacts require the identity envelope: `*_id`, `schema_version`, `created_at`, `updated_at`, and lineage fields.

---

## Config

Copy `templates/clp_config.template.yaml` to `config.yaml` in this directory and configure for your repo.
