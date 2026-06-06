# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `cl session prune` — age-based retention for ephemeral session state.
  Deletes session subdirs (and their `l-*.yaml` lease records) older than
  `--retain-days` (default 14); dry-run by default, `--apply` mutates; the
  current `$CL_SESSION_ID` always survives; `--include-archived` extends to
  `.context/archived/`.

- `cl reconcile prune --apply` now serializes per repo via an exclusive
  `.console/.reconcile.lock` (flock); a concurrent apply fails closed with
  exit code 3 instead of racing the archive append and source trim.
- `cl reconcile index --check` (with `--out`): verify the committed status
  dashboard is fresh — exit non-zero when missing or stale — so hooks/CI can
  gate on it instead of trusting the "do not hand-edit" label.

### Changed

- Onboarded the repository to the Custodian guard: added `.custodian/config.yaml`,
  pre-commit and pre-push hooks under `.hooks/`, and drove the audit to zero findings.
- Split model tests into per-module files and added unit tests for `io.yaml_io` and
  `hooks.decisions`.
- Hardened code surfaces: `subprocess.run` timeout, `json.dumps(ensure_ascii=False)`,
  `NoReturn` annotations on hook entrypoints, and a shared YAML-model loader.
- Genericized references to a private consumer repository in tracked docs/examples.

## [0.3.0]

- Session anchoring via `cl session start/show/end` with RepoGraph-backed resolution.
- Claude Code hook adapters (`cl hook pre_tool_use`, `cl hook stop`) over pure
  decision functions.
- Pydantic schemas for `InvestigationCapsule`, `LoopCheckpoint`, `WorkerHandoff`,
  and `CLConfig`.
