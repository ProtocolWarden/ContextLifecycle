# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Phase 5 consolidation engine (PROTOTYPE) — the campaign-close distill/prune
pass (spec §2.2, §2.3, §2.4, §5).

Pure stdlib + PyYAML, mirroring cold.py/route.py discipline (fail-soft, never
raises to a hook caller). The single planning entrypoint
``plan_consolidation(root, campaign_id=None, *, apply=False)`` returns a
``ConsolidationPlan`` listing planned actions; it mutates NOTHING unless
``apply=True``. The CLI prints the plan and exits 0.

DRY-RUN BY DEFAULT is the central safety property: with apply=False the gate,
decay, distill planners and prune planner are pure readers. Only apply=True
dispatches each PlannedAction to its mutator. Plan and apply share the SAME
PlannedAction list, so a test can assert on the plan and a second test can run
apply and verify the on-disk result matches.

PROMOTION GATE (spec §2.4) — the ONLY guardrail — gates SOLELY on the
unfakeable consequence-veto, NOT on confidence or citation counts (which are
retired in §2.4 and not even fields on ColdItem):
  (1) consequence.acted_on_commit is a real (non-null/non-empty) sha, AND
  (2) consequence.tests_green is exactly True ('unknown' and False both FAIL).
The spec's "or it demonstrably stopped a logged violation" path is wired as the
separate predicate stopped_logged_violation(), which ships returning False
(documented TODO) — additive, never a vote-count.

V1 PROMOTION SEMANTICS (single-survival): in this prototype an item that clears
the consequence-veto is promoted directly — passing the veto is treated as
SUFFICIENT for v1, not merely making the item a candidate. The spec frames the
veto as "necessary, not sufficient" because consequence does not certify TRUTH
(§2.4 line 213: tests only cover what they cover); a future candidacy filter
(e.g. recurrence across >1 capsule, §2.2 "distill recurring findings") would sit
between gate_promotions and the PROMOTE mapping to make eligibility genuinely
not-sufficient. That filter is intentionally deferred (the §2.6 item format
carries no recurrence-count signal, and a capsule-scan recurrence pass is out of
scope for v1), so this module does NOT claim eligibility is only candidacy.

USAGE-DECAY (spec §2.4) reads ONLY last_injected. A warm item not re-injected
within DECAY_CAMPAIGNS campaigns decays warm->cold; a hot item with no recent
match is FLAGGED hot->warm. THE PIN EXEMPTION IS ABSOLUTE: pinned==True items
are NEVER decayed regardless of last_injected.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Default decay window, expressed in CAMPAIGNS (not wall-clock — spec §2.4).
# Overridable via routes.yaml `budget.decay_after_campaigns` so all tuning lives
# in one config surface (matching route.py's max_cold_surface_per_edit).
DECAY_CAMPAIGNS = 2
ROUTES_FILE = "routes.yaml"

# PlannedAction kinds.
PROMOTE = "PROMOTE"
DISTILL = "DISTILL"
DECAY = "DECAY"
PRUNE = "PRUNE"
KEEP = "KEEP"
REJECT = "REJECT"


def _load(name: str):
    """Load a sibling engine module, package or standalone (cold.py's shim)."""
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


@dataclass(frozen=True)
class PlannedAction:
    kind: str
    target: str
    reason: str


@dataclass(frozen=True)
class ConsolidationPlan:
    promotions: list[PlannedAction] = field(default_factory=list)
    decays: list[PlannedAction] = field(default_factory=list)
    prunes: list[PlannedAction] = field(default_factory=list)
    rejections: list[PlannedAction] = field(default_factory=list)
    keeps: list[PlannedAction] = field(default_factory=list)

    def all_actions(self) -> list[PlannedAction]:
        return (
            self.promotions
            + self.decays
            + self.prunes
            + self.rejections
            + self.keeps
        )

    def is_empty(self) -> bool:
        return not self.all_actions()


# --------------------------------------------------------------------------- #
# §2.4 promotion gate — the consequence-veto.                                 #
# --------------------------------------------------------------------------- #


def stopped_logged_violation(item) -> bool:
    """Spec §2.4 alternate eligibility path: "demonstrably stopped a logged
    violation from recurring". Wired as an explicit predicate that would consult
    the §4 warn-only violation log; ships returning False (documented TODO) so
    v1 gates strictly on commit+green. Additive, NEVER a vote-count.
    """
    # TODO(§4): consult the warn-only violation log once it exists.
    return False


_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _is_real_sha(value) -> bool:
    """A v1 stand-in for real commit resolution (spec §9 open item).

    Rejects empty/placeholder values: the string must look like an abbreviated
    or full git sha (7-40 lowercase hex chars). This screens out 'TODO',
    'pending', 'see-pr', 'n/a' and other hand-written non-commits that the old
    'any non-empty string' check accepted — strengthening the unfakeability of
    the consequence-veto (§2.4). It does NOT yet resolve the sha against the repo
    (git cat-file -e); that full resolution is the deferred §9 attribution item.
    """
    return isinstance(value, str) and bool(_SHA_RE.match(value.strip()))


def gate_promotions(items) -> tuple[list, list[PlannedAction]]:
    """Apply the §2.4 consequence-veto to cold-tier items.

    Returns (eligible_items, rejection_actions). An item is promote-ELIGIBLE iff
    acted_on_commit is a real sha AND tests_green is exactly True — OR the
    additive stopped_logged_violation() path returns True. Reads NEITHER
    confidence NOR citations (not fields on ColdItem). Each rejected item yields
    a PlannedAction explaining the non-promotion.

    V1: eligibility is the whole gate — plan_consolidation promotes every
    eligible item (single-survival). See the module docstring for why the
    "necessary, not sufficient" candidacy filter is deferred.
    """
    eligible: list = []
    rejections: list[PlannedAction] = []
    for item in items:
        if getattr(item, "tier", None) != "cold":
            continue

        if stopped_logged_violation(item):
            eligible.append(item)
            continue

        if not _is_real_sha(item.acted_on_commit):
            raw = item.acted_on_commit
            if raw is None or not str(raw).strip():
                why = "acted_on_commit=null"
            else:
                why = f"acted_on_commit={raw!r} is not a sha"
            rejections.append(
                PlannedAction(REJECT, item.slug, f"ineligible: {why}")
            )
            continue
        # tests_green stored verbatim: only boolean True passes; 'unknown' /
        # False / anything else fails (the dataclass keeps it as object).
        if item.tests_green is not True:
            rejections.append(
                PlannedAction(
                    REJECT,
                    item.slug,
                    f"ineligible: tests_green={item.tests_green!r}",
                )
            )
            continue
        eligible.append(item)
    return eligible, rejections


# --------------------------------------------------------------------------- #
# §2.4 usage-decay — reads ONLY last_injected; pin exemption absolute.        #
# --------------------------------------------------------------------------- #


def _decay_threshold(context_dir: Path) -> int:
    """Read budget.decay_after_campaigns from routes.yaml, else DECAY_CAMPAIGNS."""
    try:
        data = yaml.safe_load((context_dir / ROUTES_FILE).read_text()) or {}
        budget = data.get("budget", {}) or {}
        if "decay_after_campaigns" not in budget:
            return DECAY_CAMPAIGNS
        return int(budget.get("decay_after_campaigns"))
    except (OSError, yaml.YAMLError, TypeError, ValueError, AttributeError):
        return DECAY_CAMPAIGNS


def _as_date(value):
    """Parse an ISO date (YYYY-MM-DD), or None on any malformed/non-string value.

    Tolerates a datetime-with-time component by taking the leading date token.
    Returns None rather than raising so callers fall back to a conservative
    KEEP (spec §1 never-raises bias toward not losing a fact).
    """
    from datetime import date

    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    token = s.split("T")[0].split(" ")[0]
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def _is_stale(
    last_injected,
    current_started: str | None,
    threshold: int,
    campaign_starts: list[str] | None = None,
) -> bool:
    """Stale when never injected, or injected before the threshold campaign window.

    Decay is expressed in CAMPAIGNS (spec §2.4), not wall-clock. ``campaign_starts``
    is the chronological list of recent campaign start-dates (oldest..newest);
    when supplied, the window is the ``threshold``-th most recent start: a fact is
    stale iff its last_injected predates that boundary (it has missed ``threshold``
    campaign windows). When no history is available we fall back to the single
    ``current_started`` anchor, which can only express decay-after-ONE-campaign
    (the threshold is still recorded in the decay reason, but a multi-campaign
    window cannot be honored without the start-date history — so v1 with only the
    anchor is intentionally threshold-1).

    Dates are compared as true ``date`` objects (not lexically); an unparseable
    last_injected or window boundary falls back to KEEP (return False) to avoid
    losing a fact spuriously. A fact never injected (None/empty) is stale.
    """
    if last_injected is None or not str(last_injected).strip():
        return True

    li = _as_date(last_injected)

    # Build the staleness boundary date. Prefer the campaign-start history so the
    # configured multi-campaign threshold is genuinely honored.
    boundary = None
    if campaign_starts:
        parsed = [d for d in (_as_date(c) for c in campaign_starts) if d is not None]
        parsed.sort()
        if parsed:
            n = max(1, int(threshold))
            # The boundary is the start of the n-th most recent campaign window.
            # last_injected on/after this boundary means re-exercised within the
            # window -> keep; strictly before -> it has missed n windows -> decay.
            idx = max(0, len(parsed) - n)
            boundary = parsed[idx]
    if boundary is None:
        boundary = _as_date(current_started)

    if li is None or boundary is None:
        # Cannot compare -> conservative KEEP (do not lose a fact spuriously).
        return False
    # Injected on/after the boundary => re-exercised within the window -> keep.
    if li >= boundary:
        return False
    return True


def decay_warm(
    items,
    context_dir: Path,
    current_started: str | None,
    campaign_starts: list[str] | None = None,
) -> tuple[list[PlannedAction], list[PlannedAction]]:
    """Plan §2.4 decay. Returns (decay_actions, keep_actions).

    warm + stale + not pinned        -> DECAY warm->cold.
    hot  + stale + not pinned        -> DECAY (flag) hot->warm.
    pinned (any tier)                -> KEEP (pinned) — exemption is ABSOLUTE.
    warm/hot recently injected       -> KEEP (re-exercised).
    Reads ONLY last_injected (never confidence/citations/consequence).
    """
    threshold = _decay_threshold(context_dir)
    decays: list[PlannedAction] = []
    keeps: list[PlannedAction] = []
    for item in items:
        tier = getattr(item, "tier", None)
        if tier not in ("warm", "hot"):
            continue
        if item.pinned:
            keeps.append(PlannedAction(KEEP, item.slug, "KEPT (pinned)"))
            continue
        if not _is_stale(
            item.last_injected, current_started, threshold, campaign_starts
        ):
            keeps.append(
                PlannedAction(
                    KEEP, item.slug, f"re-injected (last_injected={item.last_injected})"
                )
            )
            continue
        if tier == "warm":
            decays.append(
                PlannedAction(
                    DECAY,
                    item.slug,
                    f"warm->cold (last_injected={item.last_injected}, "
                    f"threshold={threshold} campaigns)",
                )
            )
        else:  # hot — conservative: FLAG hot->warm, not auto-demote-to-cold.
            decays.append(
                PlannedAction(
                    DECAY,
                    item.slug,
                    f"flag hot->warm (last_injected={item.last_injected})",
                )
            )
    return decays, keeps


# --------------------------------------------------------------------------- #
# The dry-run planner + apply executor.                                       #
# --------------------------------------------------------------------------- #


def plan_consolidation(
    root: Path, campaign_id: str | None = None, *, apply: bool = False
) -> ConsolidationPlan:
    """Compute (and, with apply=True, execute) the campaign-close pass.

    Always builds the ConsolidationPlan FIRST via pure readers (zero writes).
    With apply=True, dispatches each action to its mutator with per-action
    isolation. Returns an empty plan on any top-level failure (never raises).
    """
    try:
        cold = _load("cold")
        distill = _load("distill")
        prune = _load("prune")
        campaign = _load("campaign")

        context_dir = root / ".context"
        knowledge_dir = context_dir / "knowledge"
        sessions_dir = context_dir / "sessions"
        archived_dir = context_dir / "archived"

        items = cold.load_index(knowledge_dir)

        # Scope to a campaign_id when one is given (select that campaign's cold
        # items); otherwise consider all.
        if campaign_id:
            scoped = [i for i in items if i.campaign_id == campaign_id]
        else:
            scoped = items

        # Current campaign start (for decay date comparison) from task.md.
        current_started = None
        camp = campaign.parse_task(root / ".console" / "task.md")
        if camp is not None:
            current_started = camp.started

        # Best-effort campaign-start history (no dedicated store yet, §9): derive
        # each campaign's start from the earliest `created` of its cold items.
        # This lets the configured multi-campaign decay window be honored without
        # new persistent state; when no usable history is derivable, decay_warm
        # falls back to the single current_started anchor (decay-after-one).
        campaign_starts = _derive_campaign_starts(items, current_started)

        # (1) promotion gate over scoped COLD items.
        eligible, rejections = gate_promotions(scoped)
        promotions = [
            PlannedAction(
                PROMOTE,
                it.slug,
                f"eligible: acted_on_commit set & tests_green=True "
                f"-> {distill.leaf_doc_rel(it)}",
            )
            for it in eligible
        ]

        # (2) decay over ALL warm/hot items (decay is repo-wide, not scoped to a
        # single campaign — a warm fact decays on disuse regardless of which
        # campaign minted it).
        decays, keeps = decay_warm(
            items, context_dir, current_started, campaign_starts
        )

        # (3) session pruning.
        prune_plans = prune.plan_prune(sessions_dir)
        prunes = [
            PlannedAction(PRUNE, pp.sid, pp.reason)
            for pp in prune_plans
            if pp.prunable
        ]
        keeps += [
            PlannedAction(KEEP, pp.sid, pp.reason)
            for pp in prune_plans
            if not pp.prunable
        ]

        plan = ConsolidationPlan(
            promotions=promotions,
            decays=decays,
            prunes=prunes,
            rejections=rejections,
            keeps=keeps,
        )

        if not apply:
            return plan

        # ---- APPLY: dispatch each action to its mutator, per-item isolated. ----
        run_date = _today()
        by_slug = {i.slug: i for i in items}

        for action in promotions:
            try:
                item = by_slug.get(action.target)
                if item is not None:
                    distill.materialize(item, root, run_date)
            except Exception:
                continue

        for action in decays:
            try:
                item = by_slug.get(action.target)
                if item is None or item.pinned:
                    continue
                if item.tier == "warm":
                    demoted = _replace(item, tier="cold")
                    cold.write_item(knowledge_dir, demoted)
                elif item.tier == "hot":
                    flagged = _replace(item, tier="warm")
                    cold.write_item(knowledge_dir, flagged)
            except Exception:
                continue

        try:
            prune.apply_prune(prune_plans, archived_dir)
        except Exception:
            pass

        return plan
    except Exception:
        return ConsolidationPlan()


def _derive_campaign_starts(items, current_started: str | None) -> list[str]:
    """Approximate each campaign's start date from cold-item `created` dates.

    Groups items by campaign_id and takes the earliest valid `created` per
    campaign as that campaign's start. Includes current_started so the active
    campaign is represented. Returns a chronologically sorted (oldest..newest)
    list of ISO date strings — the input _is_stale uses to count campaign
    windows. Best-effort: items with no parseable created/campaign_id are
    skipped; an empty result makes decay fall back to the single anchor.
    """
    earliest: dict[str, object] = {}
    for it in items:
        cid = getattr(it, "campaign_id", "") or ""
        d = _as_date(getattr(it, "created", None))
        if not cid or d is None:
            continue
        cur = earliest.get(cid)
        if cur is None or d < cur:
            earliest[cid] = d
    dates = list(earliest.values())
    cs = _as_date(current_started)
    if cs is not None:
        dates.append(cs)
    return [d.isoformat() for d in sorted(set(dates))]


def _replace(item, **changes):
    from dataclasses import replace as _r

    return _r(item, **changes)


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


# --------------------------------------------------------------------------- #
# CLI — mirrors route.main(): print the plan, exit 0; --apply runs mutators.  #
# --------------------------------------------------------------------------- #


def _render(plan: ConsolidationPlan) -> str:
    lines: list[str] = []
    for a in plan.promotions:
        lines.append(f"PROMOTE {a.target} ({a.reason})")
    for a in plan.rejections:
        lines.append(f"  reject {a.target} ({a.reason})")
    for a in plan.decays:
        lines.append(f"DECAY {a.target} ({a.reason})")
    for a in plan.prunes:
        lines.append(f"PRUNE {a.target} ({a.reason})")
    for a in plan.keeps:
        lines.append(f"  keep {a.target} ({a.reason})")
    if not lines:
        return "consolidate: nothing to do"
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase 5 consolidation engine")
    parser.add_argument("--root", default=".", help="repo root (CL_ANCHOR)")
    parser.add_argument("--campaign", default=None, help="campaign_id to scope to")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="execute the plan (mutates files); default is dry-run",
    )
    args = parser.parse_args(argv)
    try:
        plan = plan_consolidation(
            Path(args.root), args.campaign, apply=args.apply
        )
        sys.stdout.write(_render(plan) + "\n")
    except Exception:  # never blocks: any failure -> nothing actionable, exit 0
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
