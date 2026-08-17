## 2026-08-17 — docs(links): schema references were missing a path segment

Found by Custodian's new K5 detector on its first ecosystem run.

`docs/adopting.md`'s "Schema reference" section linked
`.context/schemas/investigation_capsule.yaml` and its two siblings. From inside
`docs/` that resolves to `docs/.context/schemas/` — which does not exist. The
schemas are real and sit at the repo root, so the links needed `../`.

All three verified to resolve. Worth noting these were invisible to the
markdown-only link sweep run earlier the same day: the targets are `.yaml`, and
checking only `.md` targets is exactly the blind spot K5 was written to close.

# Log
## 2026-08-03 — fix(reconcile): give the prune lock a Windows backend

`reconcile_lock` raised `RuntimeError` without `fcntl`, so `prune --apply` was
unrunnable on Windows — which is why consumer logs got hand-pruned instead.

`msvcrt.locking(LK_NBLCK)` is the direct analogue: non-blocking, exclusive,
released on process death, conflicts with a second handle in the same process.
One difference shapes the layout — `flock` is advisory and whole-file, but a
`msvcrt` range is mandatory, so a reader touching a locked byte gets
PermissionError. The lock claims a sentinel byte at offset 1024 while the pid
stays at 0, readable by a contending run that wants to name the holder; the pid
is a fixed-width field because truncating would cross the locked range. Both
invariants have a test. Contention now matches on errno (`flock` gives
EWOULDBLOCK, `msvcrt` EACCES/EDEADLOCK); anything else re-raises as itself, so a
bad fd is never reported as "someone else holds it".

Not just an unblock: 9 pre-existing Windows failures were all this same
RuntimeError — four lock tests, five prune tests. Suite 25 failures -> 16, the
16 a strict subset of the old set (diffed by name). 457 -> 473 = 9 fixed + 7 new.

The cross-process test has the child print its own `os.getpid()`: a venv's
python.exe can be a shim, so `Popen.pid` is not always the lock holder.

## 2026-08-03 — fix(cli): stop console encoding from failing a command that succeeded

`cl reconcile check` computed a GREEN verdict, then died printing it:

    UnicodeEncodeError: 'charmap' codec can't encode character '→'

`check.py:132` renders cross-repo routing with `→`, and a default Windows
console is cp1252. So any worksheet carrying a cross-repo item — the ordinary
case, since routing work to its owning repo is the point — reported a passing
gate as a traceback. The check had already finished; only the formatting failed.

Fixed at the stream, not the glyph. Replacing `→` would have been whack-a-mole:
the source carries nine distinct non-ASCII codepoints across ~940 occurrences,
with output-bearing lines in `cli/ledger.py`, `reconcile/check.py` and
`cli/loop.py`, and any new report line could reintroduce it. `ensure_printable_
console()` prefers UTF-8 and falls back to `errors="replace"`, so output degrades
to `?` instead of raising. Installed as a Typer root callback, which runs before
every subcommand and takes no options, so the CLI surface is unchanged.

Same fix and near-identical wording as Custodian's `cli/colors.py`, which hit
this in its verbose audit report. Duplicated rather than shared — CL does not
depend on Custodian, and it is fifteen lines.

The fallback branch is not theoretical: a stream whose buffer is detached
rejects an encoding change but still accepts an errors change, so the guard
retries with errors alone rather than giving up on not-raising.

8 tests in `tests/test_console.py`. The first asserts the cp1252 stream really
does reject the report glyphs — without it the other seven could pass against a
stream that was never capable of failing. Suite 449 -> 457 passed, the 25
pre-existing failures and the 2 `cryptography` collection errors unchanged.

Custodian's audit caught two things in the first draft, both fixed: T7 (the file
was `test_console_encoding.py`, so `cli/console.py` had no parallel test) and T2
(the skip-path test asserted nothing — it now writes through the stream and
checks the content, so "skipping is a no-op" is actually verified rather than
merely not crashing). Audit is back to 0 findings.

Note for anyone reading CI here: `Lint (ruff)` is red on this branch and was
already red on `main` — 204 findings, identical count before and after this
change, none in the files touched here.

## 2026-07-17 — docs: D3 P5 — record stopped_logged_violation as spec-deferred

Resolved P5 (the last D3 phase) as a **decision, not a build**. Investigated
whether the §4 warn-only violation log `stopped_logged_violation()` would
consult exists: it does NOT — grep across CL src/docs is clean, and the only
`warn-only` reference (session/retention.py) is a rejection of the concept. The
governing spec settles it: context-injection-spec §4 (PM) states the warn-only
violation logger was "claimed to ship in v1 but never built" and is deferred
(2026-06-06) behind an explicit, still-UNMET build trigger — "a real recurring
violation worth seeding a rule from" (same bar as §7a). So P5 cannot be built
without first speculatively building the logger, which the spec itself gates and
which would be inert machinery (a consumer for a signal nothing produces).
Sharpened the `stopped_logged_violation` docstring + TODO to encode the
deferral-with-trigger so the `return False` reads as deliberate, not unfinished.
No logic change; 38/38 consolidate tests green. D3 build arc P0-A→P3 complete +
live on PM; P4 held (operator trust line); P5 spec-deferred with a recorded
trigger.

## 2026-07-16 — feat: D3 P3 two-phase write-once consequence writer (human --apply)

New `context_engine/attribution_apply.py` (460 lines; the `_load` sibling
loader is imported from attribution.py, not cloned — custodian D11 flagged
the copy on first push): the P3 writer that
dispatches the P2 plan into cold-item frontmatter — `apply_attribution(root,
plan, ...)` → `AppliedResult{applied, flipped, skipped}` (auditable,
machine-readable reasons). Two phases, both MONOTONE and RE-CHECKED at write
time (the plan may be stale; disk is re-read per item): Phase A re-loads each
planned slug fresh and skips unless still cold-tier AND `acted_on_commit` not
already a real sha (write-once at write time, not just plan time), then
`dataclasses.replace` + `cold.write_item` (atomic whole-file rewrite) with the
planned `tests_green` VERBATIM — a planned "unknown" is written "unknown",
never upgraded; nonstandard degrades to "unknown", never True. Phase B scans
disk for cold items with real sha + tests_green=="unknown" (incl. fresh
Phase-A writes — an immediate §3.5 arrival) and flips ONLY on a literal
True/False from the injectable CI seam; "unknown"→True|False at most once,
True/False never touched, no re-flip (guarded in code + tests). Never raises:
per-item failures recorded, batch continues. Phase-B identity: NO argv secret,
NO env reads — injectable providers default to `gh auth token` (gh = the auth
source of truth) and `git remote get-url origin` parsed to owner/repo
(https/ssh/scp forms); any failure ⇒ None ⇒ CI resolves "unknown" ⇒
safe-inert. Wired INSIDE the existing reviewed `--apply` boundary:
`plan_consolidation` gains additive `attribution_runner=None` (default = every
existing caller unchanged) + `ConsolidationPlan.attribution`; the CLI passes
`run_attribution` — dry-run now RENDERS the attribution plan + PENDING-CI-FLIP
worklist for the human reviewer (zero writes, fail-soft: attribution failure
never breaks the consolidation output; verified in a scaffolded-consumer sim
where attribution modules are absent), and under `--apply` the writer runs
FIRST, before the cold index loads, so `gate_promotions` sees fresh
consequences in the SAME pass (§4.3) — apply-then-gate covered both ways
(Phase-A True promotes; Phase-B flip promotes). The plan path stays
token-less (Phase A writes verbatim-as-reviewed; Phase B owns CI). NO
autonomous apply: P4 is an explicit operator decision — no new CLI, no new
mutation surface outside the human interlock. New
`tests/test_attribution_apply.py` (33 tests, all seams faked). Full suite
498 pass; ruff clean.

## 2026-07-16 — feat: D3 P2 pure attribution planner (Context-Used trailer → consequence plan)

New `context_engine/attribution.py`: `plan_attribution(root, ...)` →
`AttributionPlan` — the mechanism that decides which merged commit proves which
injected cold-memory item useful. Implements the D3 §4.1 link predicate, ALL
clauses required: (1) explicit `Context-Used: <slug>` git trailer (exact
match — attribution is NEVER inferred from temporal proximity); (2) path
corroboration — ≥1 changed file matches ≥1 of the item's `paths` globs via the
SAME matcher `surface_cold` uses (`route._glob_to_regex` + the same leading-./
normalization); (3) existence+reachability by construction — candidate commits
come ONLY from `git log origin/<default>` (bounded `--since` the earliest
injection ts, `--grep` prefilter); (4) causal ordering — author-date ≥ the
slug's earliest injection ts from the P0 `cold_slugs` ledger (a slug never
injected can NEVER attribute); (5) same-repo — only root's own git/ledger are
ever consulted. Cold-tier candidates only (§3.3 no feedback loop) and
write-once (already-real-sha items skipped). Multiple qualifying commits ⇒ the
EARLIEST (author-date, then sha) wins — first proof of usefulness,
deterministic because merged history is append-only; one commit citing many
slugs evaluates each independently (§3.2). `tests_green` resolved per attributed
sha through an injectable seam over P1 `ci_status.resolve_ci_status`
(caller-supplied repo/token; either absent ⇒ verbatim "unknown"; nonstandard
seam values degrade to "unknown", NEVER True). Every rejection carries a
machine-readable reason (`never_injected`, `cited_before_injection`,
`cited_no_path_overlap`, `not_cold_tier`, `already_attributed`,
`unknown_slug`, `no_citing_commit`, `timestamp_unparseable`). ZERO writes —
pure planner + dry-run-only CLI (no --apply; no --token on argv); never raises
(fail-soft empty plan, plan_consolidation's idiom). NO caller wired; P3
dispatches the plan through `cold.write_item` under the reviewed `--apply`
boundary. New `tests/test_attribution.py` = the §3 red-team as a matrix
(31 tests, all seams faked). Full suite 463 pass; ruff clean; custodian
0 findings (first pass flagged C29 line-budget + C16 read_text-encoding —
docstrings tightened to 498 lines, `encoding="utf-8"` added).

## 2026-07-16 — feat: D3 P0-B — self-describing citation instruction in the injected cold block

The injected cold block now teaches the reading agent the citation protocol
itself, so NO per-consumer prompt changes are needed (the protocol travels with
the data). route.py `build_context` appends one closing instruction line
(`COLD_CITATION_NOTE`) to the cold section — `(cite: if you act on a [slug]
item above, add the git trailer "Context-Used: <slug>" to that commit)` — and
ONLY when at least one real slug-bearing cold line surfaced (gated on
`cold_slugs`, so a hypothetical note-only block gets no instruction; a
warm-only block never does). Rendered-only: appended to the block string after
telemetry assembly, so `cold_surfaced` and `cold_slugs` are byte-identical to
before, and the line does not start with `[` so `_cold_slug_from_line` returns
None for it. cold.py `surface_cold` stays pure. Two new tests (presence + last
line + telemetry unchanged; absence on warm-only). Full suite 434 pass; ruff
clean.

## 2026-07-16 — feat: D3 P1 fail-closed CI-status resolver (sha → tests_green)

New standalone module `context_engine/ci_status.py`: `resolve_ci_status(owner,
repo, sha, *, token)` → `CIStatus` whose `tests_green` is exactly the
`True | False | "unknown"` contract cold.py stores verbatim. This is the CI half
of consolidate.py's deferred §9 attribution item (`_is_real_sha` today only
shape-checks the sha; it does NOT resolve it against the repo). Queries
GitHub's `commits/{sha}/check-runs` via `gh api` (matches CL's subprocess
idiom — no new dependency; caller-supplied token injected as `GH_TOKEN` into
the child `gh` env via an injectable `_run_gh` seam so tests never hit the
network, and the module reads NO env key of its own). Rollup mirrors OperationsCenter's
`get_failed_checks`/`get_incomplete_checks` (dedupe by name, keep newest id).
FAIL-CLOSED is the whole point: `True` iff ≥1 run AND all completed AND none
failing/incomplete/doubtful; `False` on any failing conclusion; `"unknown"` on
no runs / in-flight / unknown sha / missing token / non-zero exit / malformed
JSON / ANY exception (never raises, never `True` on doubt). NO caller wires it
yet (a later PR feeds the D3 consequence-writer); NO change to the promotion
gate. New `tests/test_ci_status.py` covers the full doubt matrix (21 tests).
Full suite 430 pass; ruff clean.

## 2026-07-16 — feat: D3 P0-A — surface cold-item slug + record injected slugs (attribution substrate)

The substrate for D3 "attribution scheme A" (explicit `Context-Used: <slug>`
citation). Two focused, non-breaking changes to the context engine:

- cold.py `surface_cold`: each surfaced cold line now leads with a
  machine-parseable `[<slug>]` citation token, ahead of the unchanged human
  `topic — glob — finding` content. An acting agent reading the injected line
  can now cite the exact item it used. Additive only — what surfaces and when
  is untouched.
- route.py telemetry: `_log_injection_event` records a new `cold_slugs`
  field — `[{"slug", "ts"}]` per surfaced cold item, ts = injection time — in
  ADDITION to the legacy `cold_surfaced` count and every existing field (fully
  backward compatible). Slugs are recovered from the surfaced lines via a new
  `_cold_slug_from_line` helper, which skips the `...(N more)` truncation note.

NO promotion-gate behavior change. New/updated tests: two exact-line assertions
updated to include the token, a parse-back test in test_cold_store.py, and two
telemetry tests in test_context_router.py (cold_slugs recorded + legacy fields
unchanged; truncation note excluded). Full suite 412 pass; ruff clean.

## 2026-07-14 — refactor: C2 shares one public section-extractor with signing (reviewer code_quality)

Addressed the C2 reviewer's code_quality concern + removed a real smell:
committed.py imported the PRIVATE `signing._load_section` AND duplicated its
section-extraction as `_section_from_yaml_text`. Promoted to a single public
`signing.section_of(data)` (the one source of truth for what the anchor covers)
+ `signing.load_section(file)`; committed.py now calls both, so the signed and
committed checks provably resolve the SAME scope with no duplication. Also
caught a latent bug: the committed-YAML parse could raise `yaml.YAMLError`,
which `verify_committed` (contract: never raises) did not catch — now returns
`skip` on malformed committed YAML too. 408 pass; a same-scope test pins that
`load_section` and `section_of` resolve identically (T1).

## 2026-07-14 — feat: C2 launch-time committed-truth check (keyless council anchor)

Council spec (COUNCIL_VERDICT.md) Phase 1. When the loop config has NO signed
reference, `cl loop run` now compares the live `pseudo_operator:` section
against the committed copy on `origin/main` (`git show origin/main:<relpath>`
after a bounded `git fetch`) — the keyless analogue of Track C's
restore-by-consumption. Match → run live (it IS committed truth); drift → run
the COMMITTED copy (`signed_status="drift_unsigned"`, no YAML rewrite) and flag
loudly; unreachable origin → skip with a loud note (degrade-never-halt). New
launcher flag `--require-committed` (parity with `--require-signed`, lives
outside agent-reachable config) turns it into a fail-closed gate: refuses to
start on drift AND when it cannot confirm committed truth (offline) — a
fail-open skip there would let anyone bypass the gate by cutting network. A
signed reference present ⇒ Track C wins unchanged; C2 is never consulted. New
module `pseudo_operator/committed.py` reuses signing's `_load_section` +
`canonical_bytes` so the committed scope is byte-identical to what Track C
signs. 28 tests (test_committed.py + signing/loop regression); T1 satisfied by
a `CommittedResult` isinstance assertion.

## 2026-07-07 — fix: surface hook stderr in the loop log on success

Hooks log their actions via logging → stderr (e.g. OC self-update's
"pulling and restarting watchers"), but _run_hook discarded stderr unless
the hook FAILED — deploys were invisible in the loop log and had to be
reconstructed from ps timestamps. On success the stderr tail (last line,
200 chars) is now logged as `hook <name>: ...`.

## 2026-07-07 — release: v0.4.2 (env_file shell sourcing)

Version bump for #39 so consumer pins pick up the token fix.

## 2026-07-07 — fix: env_file shell sourcing (command substitution)

First live OC run of `cl loop` flagged an invalid GITHUB_TOKEN: the env file
defines it via `$(gh auth token ...)`, which the literal line parse in
`_session_env` handed to sessions unexpanded. Env files are `source`d by the
repos' shell wrappers, so the engine now resolves them the same way — source
in a throwaway bash (`set -a; . file; env -0`, 60s timeout) and setdefault
the resulting vars (process env still wins). Literal parse kept as the
fallback when the shell fails. 2 new tests (substitution resolves; fallback
stays literal).

## 2026-07-06 — release: v0.4.1 (signed loop config)

Version bump for #37 so OC can pin the anchor-verified engine.

## 2026-07-06 — feat: signed loop config — the live-plane trust anchor (Track C)

signing.py + `cl loop sign-config` / `verify-config` + verification in
`cl loop run`: deploy-only-from-signed-reference for the pseudo_operator
section. ed25519 (EVAL key conventions; private key off-infra; pubkey hex
pinned beside the config, CODEOWNERS-protected). Drift = run the SIGNED
reference (restore-by-consumption, no fleet write path into its own
guardrails); bad signature = refuse; unsigned = loud warn, refusable with
--require-signed at the launcher. Runtime state gains signed_status.
9 tests incl. tamper/wrong-key/drift-consumption. cryptography made an
explicit dep. Consumers anchor via the operator signing ceremony (runbook in
docs/design/pseudo_operator.md).

## 2026-07-06 — release: v0.4.0 (PseudoOperator harness)

Version bump for the `cl loop` / pseudo_operator package (#34/#35) so
consumers can pin it (OC moves its git-dep pin to v0.4.0).

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

## Archived

_Archived completed history → `<private-manifest>/archive/console/ContextLifecycle/log-2026-07-06.md`_

## 2026-07-13 — budget_guard hook (v0.4.3)

Operator directive: the OC fleet+loop+supervision consumed a full claude session_5h
bucket; the system must leave ~25% unspent. New per-iteration budget_guard hook (run
before backend selection; stdout {backend: iso|null} merged EXTEND-ONLY into the
cooldown table so a budget horizon never masks a real limit reset). OC's consumer:
`loop_bridge budget-guard` (OC #452) — over-budget looks like a cooldown, ladder
diverts to codex, resumes on bucket roll.
