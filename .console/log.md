# Log
## 2026-07-06 — loop: runtime-state parity for the OperatorConsole pane

The pane renders backend_limit_kinds + sleeping_until_utc from the old OC
controller state; the engine now emits both (per-backend limit_kind/model
recorded at cooldown; sleeping window set around the all-backends-cooling
sleep). Prereq for the OC consumer migration.

## 2026-07-06 — feat: PseudoOperator harness (`cl loop`) — Track B

New context_lifecycle.pseudo_operator package + `cl loop` command group
(run/status/stop/pause/resume/signal): the shared session-loop harness
replacing the two near-copy-paste tools/loop/controller.py files in
OperationsCenter and a private downstream repo. Mechanism here (atomic hostname-aware lock, bounded
session spawn, backend cooldown/fallback ladder with the battle-tested
rate-limit parser ported verbatim, CL anchoring, ENFORCED iteration/failure
caps, adaptive delay, pause=away/lazy trigger); policy per repo via a
fail-closed `pseudo_operator:` config section (extra="forbid" — this is the
activation of the previously-inert loop config schema); repo-specific code
enters only as hook commands (pre_iteration / seed_cooldowns / on_cooldown /
session_end). Design: docs/design/pseudo_operator.md. 23 new tests.
Consumer migration (OC/a private downstream repo config + launcher swaps) lands in their repos.

## 2026-06-26 — fix: lazy fcntl import so cl runs on Windows

`reconcile/lock.py` imported `fcntl` (POSIX-only) at module load, and `cli/main.py`
eagerly imports the reconcile subcommand — so every `cl` command (incl. `session
start`) crashed at import on Windows with `ModuleNotFoundError: No module named 'fcntl'`.
Made the import lazy/guarded (`try/except ImportError`) and raise a clear error only if
`reconcile prune --apply` actually runs without fcntl. No behaviour change on POSIX.
Unblocks session anchoring on Windows hosts.

## 2026-06-17 — feat: `cl ledger observe` + `promote` (consolidation loop, self-verifying)

Closed the capture→judgment loop with two new ledger steps, both built so the
controller can run them without manufacturing judgment:

- **observe** (`ledger/observe.py`, `cl ledger observe`): clusters candidates by
  recurring signal within a window (default ≥3 in 30d), skipping signals that
  already carry a verifiable promoted judgment. Counts, never judges — surfaces
  the *novel* patterns awaiting a first human call. Exit 0 (a nudge).
- **promote** (`ledger/promote.py`, `cl ledger promote --repos-root …`): the one
  machine-allowed promotion — a *re-verification*, not a judgment. A signal earns
  its judgment once from a human, who appends a machine-readable
  `[check: custodian:<repo>:<id> | ci:<repo>:<wf>:<job> | path:<repo>:<rel>]`.
  Thereafter each recurrence auto-promotes by confirming that check still resolves
  (pure file-read, no command exec), writing a reconfirmation line that cites the
  source judgment + a `[reconfirmed: ref @ date]` stamp (distinct tag, so auto
  lines never breed further auto-promotions). A ref that stops resolving is
  reported as **regressed** (exit 1) and its candidates are left untouched — an
  encoded judgment rotted, which is a human signal. Residual free-text judgments
  (nothing to verify) stay manual. 35 new tests (observe/promote/CLI); 345 green.
  Operator decision (2026-06-17): controller may auto-promote the self-verifying
  class; free-text waits on a human.

## 2026-06-16 — feat: `cl ledger check` (staleness gate for unpromoted candidates)

Added `cl ledger check [--max-age-days N]` (default 14): lists
operator-interventions-ledger *candidates* (`- [ ] CANDIDATE …`) left unpromoted
past the age threshold and exits 1 if any, else 0. Promoted entries (the
CANDIDATE marker removed) are never flagged; unparseable dates are skipped;
fail-soft (exit 0) when no private manifest resolves. Closes the capture→promote
loop — capture without promotion is just an accreting pile. Logic in
`context_lifecycle.ledger.check`; surfaced non-blocking from PrivateManifest's
pre-push.

## 2026-06-16 — feat: `cl ledger capture` (operator-interventions ledger, capture half)

Added `cl ledger capture <signal> "<context>"` — appends an *unjudged candidate*
(`- [ ] CANDIDATE <date> <signal> — <context> — judgment: ___`) to the
operator-interventions ledger in the private manifest
(`<private-root>/ledger/operator-interventions.md`), deduped on signal+context so
a re-firing fleet signal can't pile up duplicates. Capture only ever adds
candidates; *promotion* (writing the judgment line, keep-or-drop) stays manual by
design — auto-judging would manufacture false confidence and a firehose of
unjudged rows would destroy the signal. New `context_lifecycle.ledger` package
(logic) + `cli/ledger.py` (Typer command), fail-soft via `LedgerUnavailable` when
no private manifest resolves. Intended caller: a fleet signal (OC worker observing
a human closed/overrode its PR), wired separately.

## 2026-06-15 — chore: cwd-safe ContextGuard hook in canonical adapter template

Hardened the adapter template `adapters/claude/settings.json` hook commands to
`bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/..."` so installed hooks resolve
regardless of the shell cwd. This is the source of truth every `adapters/install.sh`
+ provisioning install flows from, so future installs are cwd-safe; deployed repos
are hardened in sibling PRs. The relative path errored non-blockingly with
"No such file or directory" when a tool ran from a non-root cwd.

## 2026-06-15 — chore: enable CAP1 capability-ref enforcement

Set `audit.capabilities.enforce: true` + `capabilities.registry_repo:
../PlatformManifest` so Custodian's CAP1 detector verifies the capability this
repo owns (`session_gc`) points at invocation.ref code that resolves here —
`context_lifecycle.session.retention.apply_session_prune`. Uses `registry_repo`
(not `cross_repo`) so CAP1 turns on without also enabling the X-class cross-repo
detectors (per the decoupling in Custodian #38). The PM seed ref was corrected to
the real entrypoint in PM #76 — caught by enabling CAP1. Activates once the local
custodian install is refreshed to @main.

## 2026-06-06 — fix: port PM #68's cold.py docstring into engine source

PM #68 corrected write_item's docstring (PARKED → CLOSED-superseded) in PM's
vendored .context/.engine/cold.py copy only — `cl context init` refreshes
engine copies from THIS package, so the next refresh would have silently
reverted the fix. Lesson: engine fixes go to CL source first, consumers second.

## 2026-06-06 — feat: injection telemetry (closes the §7a instrumentation gap)

The context-management completeness audit found injection effectiveness was
write-only: the §7a KEEP verdict rested on one observed edit because nothing
records when routes fire, making any future re-evaluation data-free. route.py's
build_context now appends one JSONL event per surfaced injection (target,
injected docs, empty/missing/over-budget diagnostics, cold-surfaced count) to
<anchor>/.context/sessions/.telemetry/injection.jsonl — machine-local dot-dir
under sessions/ (fleet gitignore covers it; GC sweeps skip dot-dirs). Strictly
best-effort: telemetry failure never affects the router (spec §1 never-raises;
test proves injection survives a telemetry OSError). No event on no-match, so
the log measures fires, not edits. Consumers pick it up on next `cl context
init` engine refresh. 3 new tests; suite 296 pass.

## 2026-06-06 — fix: retention audit follow-ups (recovery window + race + tests)

Fresh post-train architecture audit confirmed the train clean except four small
retention items, fixed here: (1) manual `prune --include-archived` dated
archived dirs by id, silently bypassing the auto-GC 30-day recovery window for
freshly-moved old-id dirs — archived dirs now date by `.gc-moved-at` stamp when
present (new `_moved_at_date` helper, shared with tier 2); (2) tier-1
`shutil.move` now tolerates losing a concurrent-sweep race (OSError → skip);
(3) the stamp-before-sweep throttle semantics (one ATTEMPT per window, not one
success) are now documented in the docstring — deliberate, so a persistently
failing sweep doesn't re-pay its failure on every session start; (4) test gaps
closed: collision-suffix lifecycle, corrupt-stamp fail-safe, stamp-respecting
manual prune, throttle-after-failure. Suite 293 pass. Two audit claims REFUTED
and not acted on: DC9/DC7 count semantics are identical (agent misread DC7),
and the 44d fallback is already explained at the fallback site.

## 2026-06-06 — feat: auto-GC at session start (adversarially-reviewed design)

What drives `cl session prune` periodically: nothing did. Three adversarial
reviews settled the action: plain auto-delete REJECTED (a loop session whose id
is 15+ days old can still be writing leases — id-date is frozen at creation and
$CL_SESSION_ID only protects the starter); warn-only REJECTED on fleet evidence
(loop controller starts sessions with capture_output=True so stderr is unread;
phase-3 nudge precedent proved warn-only hygiene inert); the surviving shape is
two-stage move-then-delete. Tier 1: >14d sessions MOVE to archived/ with a
.gc-moved-at stamp — reversible, and a still-live writer self-heals by
recreating its sessions/ dir. Tier 2: archived dirs DELETE 30d after the stamp
(44d id-date fallback for `session end` archives). Trigger: inside
`cl session start`, 24h stamp throttle, whole sweep try/except (stdout carries
eval'd exports — GC must never escape), protects both env sid and the freshly
generated sid, audit lines to sessions/.gc/log (dot-dir, covered by the fleet's
sessions/*/ gitignore; all sweeps skip dot-dirs). Manual `cl session prune`
unchanged. 9 new tests (incl. stdout-purity and GC-failure-survival); suite 289
pass. Live smoke on PM: stamp written, nothing moved (all sessions <14d),
git status clean.

## 2026-06-06 — feat: cl session prune (ephemeral-tier retention)

Closes the last CL-side spec-audit tail item: PM's anchor had accumulated ~69k
l-*.yaml lease records / 276 MB under .context/sessions/ because loop/executor
sessions never call `cl session end`. New session/retention.py +
`cl session prune [MANIFEST] [--retain-days N] [--include-archived] [--apply]`:
date sessions by their id stamp (s-YYYY-MM-DD-…, mtime fallback), delete dirs
strictly older than the cutoff, always keep $CL_SESSION_ID, dry-run by default
(reconcile-prune idiom). Safe by the ephemeral-tier invariant — a session file
must never hold the only copy of anything worth keeping. Live-verified on PM:
7-day dry-run identifies 60,783 files / 50.5 MiB; default 14-day window
correctly keeps the still-young sessions. 11 new tests (T1 guard satisfied via direct PruneCandidate/SessionPrunePlan
assertions); suite 280 pass.


## Archived

_Archived completed history → `/home/dev/Documents/GitHub/PrivateManifest/archive/console/ContextLifecycle/log-2026-07-06.md`_
