# Log
## 2026-05-23 — chore: onboard Custodian (config, hooks, tests)

Brought the repo under the Custodian guard; drove the audit from 88 findings to 0 (clean).

- `.custodian/config.yaml`: added `c13_allowed_paths` (cli/session, session/anchor, session/ids — the env-reading CLI layer) and T1/T6/T7 exclusions for `errors.py` (pure exception hierarchy) and `cli/main.py` (Typer wiring entrypoint), each with a rationale comment.
- Code fixes (behavior-preserving): `subprocess.run` timeout in `session/anchor.py`; `json.dumps(ensure_ascii=False)` in `cli/hook.py` + `cli/session.py`; `NoReturn` on the two hook entrypoints; refactored the duplicate `_load_handoff`/`_load_checkpoint` bodies into a shared `_load_model` helper (D11).
- Tests: renamed existing test files to the `test_<stem>.py` parallel convention; split `test_models.py` into per-model files; added real unit tests for `io/yaml_io`, `hooks/decisions`, the nested schema components (ContextRisk/Orchestrator/RelaunchMetadata/WorkerScope/Lease/GuardConfig/LoopConfig/CapsuleExclusions/StopReport), and the module-level `session/paths` helpers. Fixed N2 (`H()` → `_hook_input`) and T2 (added assert). 111 tests pass.
- Privacy (B1): genericized references to a private consumer repository in tracked docs/examples and renamed the two files that carried the private name.
- Workspace: added venv guard to `tests/conftest.py`; added `.hooks/pre-commit` + `.hooks/pre-push` (copied from CoreRunner); fixed `.gitignore` `.console/*` policy + un-tracked `CLAUDE.md`; added `.env.example`, `CHANGELOG.md`, `SECURITY.md`, `docs/README.md`, and README `Architecture` / `What This Is Not` sections.
- Set `git config core.hooksPath .hooks`.

## 2026-05-22 — Bump repograph pin v0.2.0 → v0.2.1 (alias resolution)

Picks up `can_anchor_host` alias-resolution from RepoGraph v0.2.1. Operators may now pass canonical_name or any registered alias (snake_case dict key, case-insensitive); previously only canonical_name resolved, which masked real boundary violations behind a misleading "not registered" error.

## 2026-05-22 — fix: recognize `repos_touched` in capture boundary check

E2E anchor flow test caught: `capture(result={"repos_touched": [...]})` was silently skipping RepoGraph authorization because `_extract_repos` only recognized `repos`, `targets`, `target_repos`. The cross-boundary write (PM-anchored session → private-owned repo) wrote successfully when it should have been blocked.

Added `repos_touched` to the recognized list. All 90 tests still pass; e2e verified that `repos_touched: ["VideoFoundry"]` from a PM anchor now raises BoundaryViolation.

Follow-up not addressed here: `RepoGraph.can_anchor_host` matches on canonical_name (`VideoFoundry`) and rejects the snake_case repo key (`videofoundry`) as "unregistered". Both forms should be acceptable for UX. Filed for a separate change in RepoGraph.

## 2026-05-22 — Pin repograph to git tag v0.2.0 (was file:// local pin)

Follow-up to ADR 0002 P2/P4 release. Switched `repograph` dependency from a local file:// pin (dev-only) to `git+https://github.com/ProtocolWarden/RepoGraph.git@v0.2.0`. Reproducible across machines and CI. Local editable installs (`pip install -e ../RepoGraph`) still override the pin for active development.


## 2026-05-22 — P4: public lifecycle API (hydrate / capture / peek)

Branch: `feat/p4-public-api`. Version bumped to 0.3.0.

- `src/context_lifecycle/lifecycle.py` (NEW, ~220 LOC): implements `hydrate`,
  `capture`, `peek`, and the `HydratedContext` dataclass. Reads/writes go
  through the existing `session.paths.SessionPaths` + `io.yaml_io` helpers
  — no new I/O primitives.
  - `hydrate(lineage_id, work_item)` loads `active/<lineage_id>.yaml` if
    present (resume) or initializes a fresh capsule with `status="fresh"`,
    `created_at`, and the work_item attached. Never writes. Also includes
    the latest checkpoint (lexicographic sort of `checkpoints/*.yaml`,
    which coincides with chronological order per the P0.5 ISO-8601
    filename convention) and any active handoff matching the lineage.
  - `capture(lineage_id, result)` classifies result shape (capsule /
    checkpoint / handoff) and writes to the corresponding subdir.
    Pre-write: pulls every repo named in the result (top-level
    `repo`/`repos`/`targets` + `worker_scope.repo`) and calls
    `RepoGraph().can_anchor_host(anchor, repo)` for each. First denial
    raises `BoundaryViolation` and nothing is written.
  - `peek(work_item)` returns the active capsule dict for the work_item's
    `lineage_id` (or `lineage`/`id`), or `None`. Read-only; never falls
    back to checkpoints/handoffs.
  - All three hard-error with `AnchorMissing` / `SessionNotStarted` when
    env vars unset (P0.6 hard-error policy).
- `src/context_lifecycle/__init__.py`: re-exports the new symbols; bumps
  `__version__` to `0.3.0`.
- `tests/test_lifecycle.py` (NEW, 18 tests): hydrate fresh / resume /
  checkpoint pickup / anchor-unset / session-unset; capture for each
  subdir (capsule/checkpoint/handoff); RepoGraph called per repo;
  BoundaryViolation aborts the write atomically; no-repo capture skips
  RepoGraph entirely; peek hit / miss / no-lineage / no-write / anchor-
  unset; public API import smoke.
- Suite: 72 → 90 pass.

**Stop point:** staged, not committed. Parent handles git ops.

## 2026-05-22 — P2: wire `cl session start` through RepoGraph

Branch: `feat/p2-repograph-integration`.

- `pyproject.toml`: added `repograph @ file:///home/dev/Documents/GitHub/RepoGraph`
  as a local-path dependency (path-installed for dev; bump to pinned version
  once RepoGraph releases v0.2.0).
- `src/context_lifecycle/session/anchor.py`: replaced the P1 "Phase 2 not
  implemented" stubs. `resolve_anchor_arg(None)` now calls
  `RepoGraph().find_anchor_for_path(Path.cwd())`; bare-name args are looked
  up via `RepoGraph().authorization().get_by_name()`. `AmbiguousAnchorError`
  from RepoGraph is reraised as `AmbiguousAnchor` (mapped to CLI exit code 2
  by `cli/session.py`).
- RepoGraph is imported lazily via a `_load_repograph()` helper so tests can
  monkeypatch it and so a missing dep surfaces as a clean
  `ManifestNotFound` instead of an import crash.
- `tests/test_session_anchor.py`: replaced the four "Phase 2" stub tests
  with seven tests covering the new code paths (inference unique / none /
  ambiguous, name lookup hit / miss, path resolution unchanged).
- `tests/test_cli_session.py`: updated the no-arg test to use an empty
  `REPOGRAPH_REGISTRY` for deterministic ManifestNotFound. Suite: 72 pass.

## 2026-05-22 — P1 CLI bootstrap + bash hook port to Python (feat/p1-cli-bootstrap)

Implemented Phase 1 of the manifest-cognition work order (ADR 0002). Ported `pre_tool_use.sh` (330 lines bash) and `stop.sh` (116 lines bash) to a Python `cl` CLI shipped from CL's own `.venv/`. Bash hooks under `adapters/claude/hooks/` left untouched as the transition fallback.

**Surface delivered:**
- `bin/cl` — stable wrapper script (P0.3 contract); resolves `.venv/bin/cl`, falls back to PATH.
- `cl session start [MANIFEST] [--json|--shell|--require-clean]` with locked exit codes (0/1/2/3/4 per P0.2). No-arg invocation hard-errors with "Phase 2" guidance (RepoGraph inference deferred).
- `cl session show` / `cl session end [--archive]` for env lifecycle and per-session subdir archival.
- `cl hook pre_tool_use` / `cl hook stop` — read JSON from stdin, exit 0/2 per Claude Code hook contract, emit `{"decision":"block","reason":...}` JSON on block.

**Architecture:**
- Pydantic v2 models (`models/`) mirror the YAML schemas field-for-field; `extra="allow"` so unknown fields don't break loads. `WorkerHandoff.is_lease_expired()` accepts both `lease.expires_at` and the bash-era top-level `expires_at`.
- `hooks/pre_tool_use.py` is a pure decision function over loaded state, returning `DecisionResult(decision, reason, warnings)`. CLI layer maps to exit codes + JSON.
- Per-session subdir layout (`<anchor>/.context/sessions/<sid>/{active,checkpoints,handoffs}/`) implemented in `session/paths.py` per P0.5.
- Hard error on missing `CL_ANCHOR` (P0.6); same for `CL_SESSION_ID`. No fallback.

**Test parity:** 69 pytest tests covering every block/warn branch of the bash decision tree (require_capsule, lease expiry, forbidden/allowed paths, mutation_policy, max_subagents, high_parallelism, subagent_heavy, checkpoint_stale, long_lived_session, reload_scope_too_large), plus model round-trips, session id/anchor/paths, CLI session + hook commands. All green.

**Stop point:** changes staged (not committed). Parent reviews + commits. P2 (RepoGraph registry + `can_anchor_host` + `find_anchor_for_path`) is the next gate; once that lands, `cl session start` no-arg inference and the manifest-name lookup activate.

## 2026-05-22 — Manifest concept + CL/Manifest/RepoGraph contract split (design session)

Walked the architecture for how CL should integrate with executors and where cognition state actually lives. Result: a new repo-type vocabulary and a clean three-way contract split.

**Repo type: "manifest"** — locked in as a first-class repo category. A manifest is a repo whose job is to (1) declare what's in an ecosystem, (2) host that ecosystem's cognition state, and (3) anchor sessions that operate on it. Current instances: `PlatformManifest` (public scope), `PrivateManifest` (private scope, superset). The flow rules generalize beyond cognition to any state a manifest hosts.

**Visibility / info-flow rule:** a manifest can host state involving any repo at or below its visibility scope. Private can host cognition about public repos; public cannot host cognition about private ones. RepoGraph enforces this on writes.

**Contract split:**
- **ContextLifecycle (CL)** — owns schema + I/O + policy enforcement (the 330-line `pre_tool_use.sh` decision tree, rewritten in Python and shipped as a CLI from CL's own `.venv/`)
- **RepoGraph** — owns repo↔manifest authorization; validates every cognition write against the active session anchor's scope
- **Manifest** — session anchor + `.context/` host; data lives here, not scattered across consumer repos
- **Consumer repo (executors, etc.)** — ships a ~1-line shim hook (`exec "$CL_HOME/.venv/bin/cl" hook pre_tool_use "$@"`) and nothing else; no `.context/` data, no CL imports

**Session anchoring (two-layer):**
1. *Picking* is UX — operator launches with an explicit anchor via `cl session start <manifest>` (sets `CL_ANCHOR` env var in the session shell). Hard error if missing — no silent unguarded sessions.
2. *Enforcing* is RepoGraph — every write is validated against the anchor's scope, so mis-selects are caught after the fact. Picking and enforcing are decoupled.

**Reverts implied:**
- TE / DE / CE all have shallow CLP scaffolding (config + hooks, no Python integration) — to be removed once the CL CLI + shim pattern lands. They're library-only, so harness hooks are unnecessary there; the shim will be the only CL surface in those repos.
- Anything CL-shaped reaching into executor *code* (none exists today) stays banned. Executors don't import CL.

**`.console/` vs `.context/`** — `.console/` stays per-repo (operational truth: task/guidelines/backlog/log). `.context/` moves to the anchoring manifest (durable cognition: capsules/checkpoints/handoffs). `.console/` compaction is a future need, not addressed here.

**Stop point — implementation specs still open:**
- Final `CL_ANCHOR` env var name + `cl session start` CLI signature (incl. `--json`, RepoGraph-inferred default behavior)
- Shim's resolution path for `cl` (`CL_HOME` env, PATH symlink, or both)
- RepoGraph's authorization schema — how PM/PrivM declare repo membership in a way RepoGraph can read for visibility checks
- Layout of manifest `.context/` when hosting multiple concurrent work loops (capsule namespacing across repos)

Architectural design considered done. Next session: pick implementation specs and either update ADR 0005 or write a new ADR for the manifest concept.

## 2026-05-21 — Gitignore .console/.context

`.console/.context` is regenerated on every session launch (timestamp changes unconditionally). Added it to `.gitignore` and untracked it with `git rm --cached`. All other repos (OC, RxP, etc.) already gitignore it; CLP was the only outlier. Source truth remains `.console/{task,guidelines,backlog,log}.md`.

## 2026-05-21 — Add closing fence to console-context block

Added <!-- /console-context --> end marker so OperatorConsole only replaces its
managed block and leaves repo-owned content below it untouched.

_Chronological continuity log. Decisions, stop points, what changed and why._
_Not a task tracker — that's backlog.md. Keep entries concise and dated._

## Recent Decisions

_Log significant choices here so they survive context resets._

| Decision | Rationale | Date |
|----------|-----------|------|
| [what was decided] | [why] | [date] |

## Stop Points

_Where did you leave off? What should be verified next session?_

- [what to pick up next]

## Notes

_Free-form scratch. Clear periodically — old entries can be deleted once no longer relevant._

---

## 2026-05-24 — Hooks hard-require CL_ANCHOR (no CWD fallback)

- pre_tool_use.sh + stop.sh now resolve .context under CL_ANCHOR (the manifest anchor) instead of git-toplevel/pwd. pre_tool_use BLOCKS when CL_ANCHOR is unset; stop.sh skips gracefully. Closes the leak that let un-anchored workspace-root .context exist (now retired). Added hook test C-23 (unset→block); 23/23 pass.
- Updated verification/cross-pr-wiring-review.md G-08: workspace-root .context retired.

## 2026-05-24 — Tracked adapter installer (Phase 1 of multi-CLI CL integration)

- Added adapters/install.sh (committed, idempotent, re-syncs drift): copies the canonical claude hooks into a repo .claude/hooks/ and merges the hook wiring into settings.json (preserving other keys). Fixes the divergent per-repo hook copies + the untracked-settings problem. --cli flag is extensible; codex/aider skip gracefully until their adapters land (phase 2/3). Test: adapters/install_test.sh (6/6).
- Foundation for: codex adapter (plugin/MCP), aider adapter (session-boundary), and rewiring OC panes + OC/VF loops to anchor via `cl session start` (= owning manifest, RepoGraph-resolved). RepoGraph already provides repo→manifest (repo_owner map).

## 2026-05-24 — `cl context` CLI (Phase 2: session-boundary cognition)

- Added `cl context hydrate|capture|peek` (cli/context.py) wrapping lifecycle.hydrate/capture/peek, so non-hook CLIs (aider; codex until a native plugin) integrate at session edges. JSON args accept literal/@file/stdin. Tests: tests/test_context_cli.py (7). cli/context.py added to T1/T6/T7 shim exclusions (Typer wiring; _load_json unit-tested, commands via CliRunner).
- NOTE: codex native per-tool plugin deferred — codex hooks are a plugin (post-cutoff format); codex uses session-boundary for now (decision-compliant fallback).

## 2026-06-03 — Clear pre-existing ruff debt blocking CI

CL main CI has been red since 2026-05-30 on `ruff check .` (10 errors: unused imports in cli/hook.py, cli/session.py, and several test files; 2 E402 in conftest.py). Autofixed the unused imports; conftest's two imports are intentionally after the venv guard, so marked `# noqa: E402`. Pure lint cleanup, no behavior change — unblocks a green CI for the context-engine productionization PR.

## 2026-06-03 — Productionize the context-injection engine into CL

Moved the context-injection engine from its PlatformManifest prototype home (.context/.engine/) into CL as the canonical source, per the work-order productionization track (spec §1/§6). New: src/context_lifecycle/context_engine/ (route, cold, consolidate, distill, campaign, prune — copied byte-identical from PM; they already carry dual-mode importlib shims so they run both as a package and as scaffolded standalone files), plus __init__.py (ENGINE_DIR/ENGINE_FILES/engine_source_files) and scaffold.py (init_context). Added `cl context init` (cli/context.py): idempotently scaffolds .context/routes.yaml (stamped engine_compat), docs/inject/README, .context/knowledge/README, and copies the engine into the repo's .context/.engine/ (engine refreshed each run; authored state never clobbered). Ported the three validated hook blocks into CL's CANONICAL adapters/claude/hooks/{pre_tool_use,stop}.sh so install.sh propagates them to every repo (PM's splices were a clean superset of the adapter base, so copied verbatim). Ported engine tests (test_context_router/cold_store/consolidate) repointed at the package; PM-specific consumer-content tests dropped/made hermetic; added test_scaffold.py. Custodian: marked context_engine's 6 modules VENDORED (shipped byte-identical to consumers, run by path, tested via importlib) — excluded from src-quality detectors with documented rationale; prune.py added to c13_allowed_paths. 227 tests green, ruff + custodian clean. Engine scaffold byte-match test asserts the package stays identical to what's scaffolded. NOTE: PM's .context/.engine/ should be re-synced via `cl context init` so it tracks CL going forward (reconciliation step).
