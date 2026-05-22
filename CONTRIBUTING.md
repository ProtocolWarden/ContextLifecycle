# Contributing to ContextLifecycle

ContextLifecycle is a generic cognition lifecycle runtime for bounded, resumable agent sessions. It defines schemas, enforcement semantics, and runtime adapters — not OC/VF-specific scaffolding.

## Before You Start

- Check open issues to avoid duplicate work
- For significant schema changes or new adapters, open an issue first to discuss the approach
- All schema changes must preserve backward compatibility or bump `schema_version`
- New adapters must implement the full adapter contract (`docs/adapters/adapter_contract.md`)

## What Belongs Here

**In scope:**
- Generic schema improvements (InvestigationCapsule, LoopCheckpoint, WorkerHandoff, SessionLease)
- New runtime adapters (Codex, Aider, subprocess, CI/CD)
- ContextGuard policy improvements
- Generic examples and templates
- Documentation improvements

**Out of scope:**
- OC/VF-specific configs or escalation policies
- Private operational state
- Machine-specific assumptions

## Schema Changes

- All schemas live in `.context/schemas/`
- Templates live in `.context/templates/`
- Bump `schema_version` for any breaking field changes
- Add migration notes to the schema file header if fields are removed or renamed
- Update the corresponding template and at least one example

## New Adapters

1. Create `adapters/<runtime>/` directory
2. Implement all hooks defined in `docs/adapters/adapter_contract.md`
3. Add a `README.md` in the adapter directory
4. Add an entry to the adapter table in `docs/context_guard.md`

## Pull Request Checklist

- [ ] Schema changes preserve identity fields (`*_id`, `schema_version`, `created_at`, `updated_at`)
- [ ] Templates updated to match schema changes
- [ ] At least one example updated
- [ ] ContextGuard adapter contract still satisfied (if adapter touched)
- [ ] No private repo names, escalation tiers, or machine-specific paths in committed files

## Commit Style

Follow existing commit messages in the repo. Short imperative subject line. No trailing summaries.
