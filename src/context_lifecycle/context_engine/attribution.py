# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""D3 P2 — pure, dry-run-only attribution planner (Context-Used trailer →
consequence plan).

Decides which merged commit proves which injected cold-memory item useful,
via the D3 §4.1 link predicate — item ``S`` attributes to commit ``C`` iff
ALL hold (any failure ⇒ a machine-readable rejection, never a guess):

  1. **Explicit citation** — ``C`` carries a ``Context-Used: S`` git trailer
     (exact slug). Attribution is NEVER inferred from temporal proximity
     (§3.1 temporal-coincidence defense).
  2. **Path corroboration** — ≥1 file changed in ``C`` matches ≥1 of ``S``'s
     ``paths`` globs, with the SAME matcher ``cold.surface_cold`` uses
     (``route._glob_to_regex``). Cited-but-no-overlap = probable mis-cite ⇒
     ``cited_no_path_overlap`` (§3.2 confounding: each cited slug must
     corroborate independently).
  3. **Existence + reachability by construction** — candidates come ONLY from
     ``git log origin/<default>``, so every candidate sha resolves and is an
     ancestor of the merged default branch (§3.6 forged-sha defense).
  4. **Causal ordering** — ``C``'s author-date ≥ the slug's earliest injection
     ts from the P0 ``cold_slugs`` ledger (injection.jsonl). A never-injected
     slug can NEVER attribute; time is only ever a negative guard.
  5. **Same repo/anchor** — only ``root``'s own git repo and ledger are ever
     consulted (§3.7 cross-repo-bleed defense).

Scope guards (§3.3 feedback-loop defense): candidates are cold-tier items only
(a promoted item is out of scope forever — no "promoted ⇒ more consequence"
loop; warm/hot citations reject ``not_cold_tier``) and write-once (an item
whose ``acted_on_commit`` is already a real sha rejects ``already_attributed``).
Multiple qualifying commits ⇒ the EARLIEST (author-date, then sha) wins: the
first proof of usefulness, deterministic across re-runs because merged history
is append-only. One commit citing many slugs evaluates each independently.

``tests_green`` for the chosen sha comes through an injectable CI seam
(default: ``ci_status.resolve_ci_status``; ``github_repo``/``token`` are
caller-supplied, either absent ⇒ ``"unknown"``). Recorded VERBATIM —
``True | False | "unknown"`` — never coerced, never defaulted ``True``;
``"unknown"`` is a valid planned value (§3.5 two-phase: Phase A writes sha +
``"unknown"``; a later pass flips it when CI resolves).

PURE PLANNER: ZERO filesystem writes; never raises (unexpected failure ⇒
empty plan, ``consolidate.plan_consolidation``'s fail-soft idiom). A later P3
dispatches the plan through ``cold.write_item`` under the reviewed ``--apply``
boundary; nothing here mutates anything.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# The verbatim sentinel cold.py stores when CI cannot be resolved (matches
# ci_status.UNKNOWN; duplicated here so the planner loads standalone).
UNKNOWN = "unknown"

# The git trailer key an acting agent uses to cite an injected cold item.
TRAILER_KEY = "Context-Used"

# Machine-readable rejection reasons (the §3 red-team, as codes).
REASON_ALREADY_ATTRIBUTED = "already_attributed"
REASON_NEVER_INJECTED = "never_injected"
REASON_NO_CITING_COMMIT = "no_citing_commit"
REASON_CITED_BEFORE_INJECTION = "cited_before_injection"
REASON_CITED_NO_PATH_OVERLAP = "cited_no_path_overlap"
REASON_NOT_COLD_TIER = "not_cold_tier"
REASON_UNKNOWN_SLUG = "unknown_slug"
REASON_TIMESTAMP_UNPARSEABLE = "timestamp_unparseable"

# Bounded so a slow git never hangs the caller (mirrors pseudo_operator/
# committed.py's bounded _run_git idiom).
_GIT_TIMEOUT_SECONDS = 30

# One record per commit: sha, author-date (strict ISO, %aI), then the
# Context-Used trailer values — all \x1f-separated, records \x1e-terminated.
_LOG_FORMAT = (
    "%H%x1f%aI%x1f%(trailers:key=" + TRAILER_KEY + ",valueonly,separator=%x1f)%x1e"
)

# The same sha shape-check the promotion gate applies (consolidate._is_real_sha);
# duplicated as a regex so this module loads standalone next to cold/route.
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _load(name: str):
    """Load a sibling engine module, package or standalone (consolidate's shim)."""
    try:  # pragma: no cover - import shim
        return __import__(name)
    except ImportError:  # pragma: no cover
        p = Path(__file__).resolve().parent / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"cl_{name}", p)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault(f"cl_{name}", mod)
        spec.loader.exec_module(mod)
        return mod


# ---- Plan shapes — mirror consolidate.ConsolidationPlan / PlannedAction. ---- #


@dataclass(frozen=True)
class CitedCommit:
    """One merged default-branch commit carrying ≥1 Context-Used trailer.
    ``author_date`` is strict-ISO (git ``%aI``); ``slugs`` are the verbatim
    trailer values. Produced ONLY from ``git log origin/<default>`` — which is
    what makes §4.1 existence + reachability hold by construction.
    """

    sha: str
    author_date: str
    slugs: tuple[str, ...]


@dataclass(frozen=True)
class PlannedAttribution:
    """One (slug, sha, tests_green) the P3 writer WOULD write — nothing is
    written in P2. ``tests_green`` is verbatim ``True | False | "unknown"``.
    """

    slug: str
    sha: str
    tests_green: object


@dataclass(frozen=True)
class AttributionRejection:
    """One rejected (slug[, sha]) with a machine-readable reason code. ``sha``
    is the citing commit for per-commit rejections, None for slug-level ones.
    """

    slug: str
    sha: str | None
    reason: str


@dataclass(frozen=True)
class AttributionPlan:
    attributions: list[PlannedAttribution] = field(default_factory=list)
    rejections: list[AttributionRejection] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.attributions and not self.rejections


# ---- Injectable evidence seams (defaults: bounded git / ci_status). -------- #


def _run_git(
    args: list[str], cwd: Path, timeout: int = _GIT_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _git_cited_commits(
    root: Path, default_branch: str, since: str | None
) -> list[CitedCommit]:
    """Default commit seam: list merged default-branch commits citing any slug.

    Listing ``origin/<default_branch>`` IS the ancestor-of-merged-default
    requirement (§4.1.3): an unmerged-branch commit or forged sha can never
    appear here. ``--grep`` pre-filters cheaply; ``since`` (earliest injection
    ts) bounds the walk. [] on any git failure — no evidence ⇒ no attribution.
    """
    args = [
        "log",
        f"origin/{default_branch}",
        f"--format={_LOG_FORMAT}",
        f"--grep={TRAILER_KEY}:",
    ]
    if since:
        args.append(f"--since={since}")
    proc = _run_git(args, root)
    if proc.returncode != 0:
        return []
    out: list[CitedCommit] = []
    for record in proc.stdout.split("\x1e"):
        fields = [f.strip() for f in record.split("\x1f")]
        if len(fields) < 2 or not _SHA_RE.match(fields[0]):
            continue
        slugs = tuple(s for s in fields[2:] if s)
        if not slugs:
            continue
        out.append(CitedCommit(sha=fields[0], author_date=fields[1], slugs=slugs))
    return out


def _git_changed_files(root: Path, sha: str) -> tuple[str, ...]:
    """Default changed-files seam: the repo-relative paths ``sha`` touched
    (``git diff-tree`` over the single commit). () on any git failure — no
    evidence ⇒ no path overlap ⇒ the commit is rejected, never guessed.
    """
    proc = _run_git(["diff-tree", "--no-commit-id", "--name-only", "-r", sha], root)
    if proc.returncode != 0:
        return ()
    return tuple(line.strip() for line in proc.stdout.splitlines() if line.strip())


def _default_resolve_tests_green(
    github_repo: str | None, sha: str, token: str | None
) -> object:
    """Default CI seam: ``ci_status.resolve_ci_status`` for the attributed sha.
    ``github_repo`` (``owner/repo``) and ``token`` are caller-supplied — this
    module never reads the environment. Without either, the resolver cannot
    vouch for green ⇒ verbatim ``"unknown"`` (NEVER defaulted ``True``).
    """
    if not github_repo or "/" not in github_repo:
        return UNKNOWN
    owner, repo = github_repo.split("/", 1)
    ci = _load("ci_status")
    return ci.resolve_ci_status(owner, repo, sha, token=token).tests_green


# ---- Evidence readers (pure). ----------------------------------------------- #


def _as_dt(value: object) -> datetime | None:
    """Parse an ISO timestamp to an aware datetime, or None (fail-closed).

    The injection ledger writes naive local timestamps (route.py
    ``datetime.now().isoformat``) while git ``%aI`` carries an offset; a naive
    value is interpreted as local time so the two are comparable. Unparseable
    ⇒ None ⇒ the caller rejects rather than guesses.
    """
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def _earliest_injections(root: Path) -> dict[str, datetime]:
    """Earliest recorded injection time per cold slug, from the P0 ledger
    (``injection.jsonl`` ``cold_slugs`` records, route._log_injection_event).
    Malformed lines/records are skipped; a missing ledger ⇒ {} ⇒ nothing is
    attributable (``never_injected`` for every candidate). Read-only.
    """
    ledger = root / ".context" / "sessions" / ".telemetry" / "injection.jsonl"
    try:
        lines = ledger.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    earliest: dict[str, datetime] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        records = event.get("cold_slugs")
        if not isinstance(records, list):
            continue
        for rec in records:
            if not isinstance(rec, dict):
                continue
            slug = rec.get("slug")
            ts = _as_dt(rec.get("ts"))
            if not isinstance(slug, str) or not slug or ts is None:
                continue
            cur = earliest.get(slug)
            if cur is None or ts < cur:
                earliest[slug] = ts
    return earliest


def _matches_any_glob(changed: tuple[str, ...], globs: tuple[str, ...], cold) -> bool:
    """§4.1.2 path corroboration with surface_cold's EXACT matcher semantics:
    ``cold._glob_to_regex`` (the shared route.py matcher) plus the same
    leading-``./`` normalization, so "the commit touched the item's paths"
    means precisely what "this item surfaces for that path" means.
    """
    for raw in changed:
        target = raw.removeprefix("./")
        for glob in globs:
            if cold._glob_to_regex(glob).match(target):
                return True
    return False


# --------------------------------------------------------------------------- #
# The planner.                                                                 #
# --------------------------------------------------------------------------- #


def plan_attribution(
    root: Path,
    *,
    default_branch: str = "main",
    github_repo: str | None = None,
    token: str | None = None,
    list_cited_commits: Callable[
        [Path, str, str | None], list[CitedCommit]
    ] = _git_cited_commits,
    changed_files: Callable[[Path, str], tuple[str, ...]] = _git_changed_files,
    resolve_tests_green: Callable[
        [str | None, str, str | None], object
    ] = _default_resolve_tests_green,
) -> AttributionPlan:
    """Compute the attribution plan for ``root``'s cold store. ZERO writes.

    Every ambiguity resolves to do-not-attribute (precision over recall):
    under-attribution preserves the safe-inert status quo, over-attribution
    poisons durable memory. Empty plan on any unexpected failure — never
    raises (consolidate.plan_consolidation's fail-soft idiom). The three
    evidence seams are injectable so tests exercise the whole §3 red-team
    without a repo or network; defaults shell to bounded git in ``root`` (and
    ONLY ``root`` — §4.1.5 same-repo scoping) and to ci_status with the
    caller-supplied ``github_repo``/``token`` (either absent ⇒ ``"unknown"``).
    """
    try:
        cold = _load("cold")
        items = cold.load_index(root / ".context" / "knowledge")
        by_slug = {item.slug: item for item in items}
        injections = _earliest_injections(root)

        attributions: list[PlannedAttribution] = []
        rejections: list[AttributionRejection] = []

        # Candidate set: cold-tier only (§3.3), write-once (§4.4).
        candidates: dict[str, object] = {}
        for item in sorted(items, key=lambda i: i.slug):
            if item.tier != "cold":
                continue
            if _SHA_RE.match(str(item.acted_on_commit or "").strip()):
                rejections.append(
                    AttributionRejection(item.slug, None, REASON_ALREADY_ATTRIBUTED)
                )
                continue
            candidates[item.slug] = item

        # Evidence: cited commits from the merged default branch, bounded to
        # the earliest injection. With no recorded injection there is nothing
        # any commit could have been caused by — skip the walk entirely.
        commits: list[CitedCommit] = []
        if candidates and injections:
            since = min(injections.values()).isoformat()
            commits = list(list_cited_commits(root, default_branch, since))

        # Deterministic commit order: author-date, then sha. Unparseable dates
        # sort last and are rejected per-commit below.
        def _commit_key(c: CitedCommit) -> tuple:
            dt = _as_dt(c.author_date)
            return (dt is None, dt or datetime.max.replace(tzinfo=UTC), c.sha)

        commits.sort(key=_commit_key)

        # Citations of slugs outside the candidate set (audit visibility);
        # already-attributed cold items are covered slug-level above.
        seen_noncandidate: set[tuple[str, str]] = set()
        for commit in commits:
            for slug in commit.slugs:
                if slug in candidates or (slug, commit.sha) in seen_noncandidate:
                    continue
                seen_noncandidate.add((slug, commit.sha))
                cited_item = by_slug.get(slug)
                if cited_item is None:
                    rejections.append(
                        AttributionRejection(slug, commit.sha, REASON_UNKNOWN_SLUG)
                    )
                elif cited_item.tier != "cold":
                    rejections.append(
                        AttributionRejection(slug, commit.sha, REASON_NOT_COLD_TIER)
                    )

        # The link predicate, per candidate.
        for slug in sorted(candidates):
            item = candidates[slug]
            injected_at = injections.get(slug)
            if injected_at is None:
                rejections.append(
                    AttributionRejection(slug, None, REASON_NEVER_INJECTED)
                )
                continue

            citing = [c for c in commits if slug in c.slugs]
            if not citing:
                rejections.append(
                    AttributionRejection(slug, None, REASON_NO_CITING_COMMIT)
                )
                continue

            chosen: CitedCommit | None = None
            for commit in citing:  # already earliest-first
                author_dt = _as_dt(commit.author_date)
                if author_dt is None:
                    rejections.append(
                        AttributionRejection(
                            slug, commit.sha, REASON_TIMESTAMP_UNPARSEABLE
                        )
                    )
                    continue
                if author_dt < injected_at:
                    rejections.append(
                        AttributionRejection(
                            slug, commit.sha, REASON_CITED_BEFORE_INJECTION
                        )
                    )
                    continue
                if not _matches_any_glob(
                    tuple(changed_files(root, commit.sha)), item.paths, cold
                ):
                    rejections.append(
                        AttributionRejection(
                            slug, commit.sha, REASON_CITED_NO_PATH_OVERLAP
                        )
                    )
                    continue
                # Earliest qualifying commit wins (first proof of usefulness;
                # deterministic — merged history is append-only). Later
                # qualifying commits are simply not evaluated further.
                chosen = commit
                break

            if chosen is None:
                continue  # every citing commit was rejected with its reason

            tests_green = resolve_tests_green(github_repo, chosen.sha, token)
            # Verbatim contract: True | False | "unknown" only. Anything else
            # a nonstandard seam returns degrades to "unknown" — fail-closed,
            # NEVER to True (a truthy non-True like "true"/1 must not pass).
            if tests_green is not True and tests_green is not False:
                tests_green = UNKNOWN
            attributions.append(PlannedAttribution(slug, chosen.sha, tests_green))

        return AttributionPlan(attributions=attributions, rejections=rejections)
    except Exception:
        # Fail-soft: the planner must never raise to its caller; no evidence
        # beats wrong evidence (do-not-attribute is always safe).
        return AttributionPlan()


# --------------------------------------------------------------------------- #
# CLI — mirrors consolidate.main(): print the plan, exit 0. DRY-RUN ONLY:      #
# there is no --apply here at all; P3 owns mutation.                           #
# --------------------------------------------------------------------------- #


def _render(plan: AttributionPlan) -> str:
    lines: list[str] = []
    for a in plan.attributions:
        lines.append(f"ATTRIBUTE {a.slug} -> {a.sha} (tests_green={a.tests_green!r})")
    for r in plan.rejections:
        at = f" @ {r.sha}" if r.sha else ""
        lines.append(f"  reject {r.slug}{at} ({r.reason})")
    if not lines:
        return "attribution: nothing to attribute"
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="D3 attribution planner (dry-run only; zero writes)"
    )
    parser.add_argument("--root", default=".", help="repo root (CL_ANCHOR)")
    parser.add_argument(
        "--default-branch", default="main", help="merged default branch name"
    )
    parser.add_argument(
        "--github-repo",
        default=None,
        help="owner/repo for CI resolution (omitted => tests_green='unknown')",
    )
    args = parser.parse_args(argv)
    try:
        # No --token flag: a secret does not belong on argv. The CLI plans
        # Phase-A-shaped (tests_green='unknown'); a wired caller supplies the
        # token programmatically.
        plan = plan_attribution(
            Path(args.root),
            default_branch=args.default_branch,
            github_repo=args.github_repo,
            token=None,
        )
        sys.stdout.write(_render(plan) + "\n")
    except Exception:  # never blocks: any failure -> nothing actionable, exit 0
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
