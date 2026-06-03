# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the Phase 5 consolidation engine (.context/.engine/).

Covers (spec §2.2, §2.2b, §2.3, §2.4, §5):
  - campaign.parse_task / boundary_changed (front-matter'd + plain template);
  - the §2.4 consequence-veto promotion gate (acted+green promotes; un-acted /
    red / unknown does not; no confidence/citation field participates);
  - usage-decay reads ONLY last_injected; the pin exemption is absolute;
  - cold->warm distill produces a valid leaf doc + idempotent routes entry and
    flips the cold item so cold.surface_cold no longer surfaces it;
  - session pruning identifies terminal orphans, keeps the active session and
    non-terminal dirs, moves (not rm) to archived/ on apply;
  - DRY-RUN mutates NOTHING (byte-identical tree); apply matches the plan;
  - the never-raises discipline on malformed inputs;
  - a read-only smoke test against the REAL repo (defends the constraint).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_ENGINE_DIR = Path(__file__).resolve().parent.parent / "src" / "context_lifecycle" / "context_engine"
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _ENGINE_DIR / filename)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# Register under the names the engine's import shims resolve: route's lazy cold
# import and cold's `from route import ...` both use these module names, and
# consolidate.py's _load() uses __import__(name) for 'cold'/'route'/'distill'/
# 'campaign'/'prune'.
route = _load("cl_route", "route.py")
route_alias = _load("route", "route.py")
cold = _load("cold", "cold.py")
campaign = _load("campaign", "campaign.py")
prune = _load("prune", "prune.py")
distill = _load("distill", "distill.py")
consolidate = _load("consolidate", "consolidate.py")


# --------------------------------------------------------------------------- #
# fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #

ROUTES_YAML = """engine_compat: ">=0.2 <0.3"
budget:
  max_docs_per_edit: 3
routes:
  - match: "src/platform_manifest/loader.py"
    inject: ["docs/inject/loader.md"]
    priority: 10
"""

TASK_WITH_FM = """---
campaign_id: c-2026-06-03-abcd
status: active
started: 2026-06-03
---

# Task

## Objective

Build the consolidation engine.
"""

TASK_PLAIN = """# Task

## Objective

Build the consolidation engine.
"""


def _cold_item(slug, **over):
    base = dict(
        topic=over.pop("topic", slug),
        paths=("src/platform_manifest/x/**",),
        created="2026-06-01",
        campaign_id="c-2026-06-03-abcd",
        acted_on_commit=None,
        tests_green="unknown",
        tier="cold",
        pinned=False,
        last_injected=None,
        finding=f"Finding for {slug}.",
    )
    base.update(over)
    base["slug"] = slug
    return cold.ColdItem(**base)


def _write_item(knowledge_dir, item):
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    return cold.write_item(knowledge_dir, item)


def _capsule(status):
    return f"status: {status}\nsession_id: x\n"


def _snapshot(root: Path) -> dict[str, bytes]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = p.read_bytes()
    return out


# --------------------------------------------------------------------------- #
# campaign.py — §2.2b                                                          #
# --------------------------------------------------------------------------- #


def test_parse_task_frontmatter(tmp_path):
    t = tmp_path / "task.md"
    t.write_text(TASK_WITH_FM)
    c = campaign.parse_task(t)
    assert c is not None
    assert c.campaign_id == "c-2026-06-03-abcd"
    assert c.status == "active"
    assert c.started == "2026-06-03"
    assert "consolidation engine" in c.objective


def test_parse_task_plain_template(tmp_path):
    t = tmp_path / "task.md"
    t.write_text(TASK_PLAIN)
    c = campaign.parse_task(t)
    assert c is not None
    assert c.campaign_id is None
    assert c.status == "active"
    assert c.started is None
    assert "consolidation engine" in c.objective


def test_parse_task_missing_returns_none(tmp_path):
    assert campaign.parse_task(tmp_path / "nope.md") is None


def test_boundary_changed_campaign_id(tmp_path):
    t = tmp_path / "task.md"
    t.write_text(TASK_WITH_FM)
    assert campaign.boundary_changed(t, last_seen_campaign_id="c-old") is True
    assert (
        campaign.boundary_changed(t, last_seen_campaign_id="c-2026-06-03-abcd")
        is False
    )


def test_boundary_changed_status_done(tmp_path):
    t = tmp_path / "task.md"
    t.write_text(TASK_WITH_FM.replace("status: active", "status: done"))
    # Same campaign_id as last-seen, but status flipped to done -> boundary.
    assert (
        campaign.boundary_changed(t, last_seen_campaign_id="c-2026-06-03-abcd")
        is True
    )


def test_boundary_changed_objective_fallback(tmp_path):
    t = tmp_path / "task.md"
    t.write_text(TASK_PLAIN)
    h = campaign.objective_hash(campaign.parse_task(t).objective)
    # Same objective -> no boundary.
    assert campaign.boundary_changed(t, last_seen_objective_hash=h) is False
    # Changed objective -> boundary.
    t.write_text(TASK_PLAIN.replace("consolidation engine", "something else"))
    assert campaign.boundary_changed(t, last_seen_objective_hash=h) is True


def test_boundary_changed_unreadable_is_false(tmp_path):
    assert campaign.boundary_changed(tmp_path / "missing.md", "x") is False


# --------------------------------------------------------------------------- #
# consolidate.gate_promotions — §2.4 consequence-veto                         #
# --------------------------------------------------------------------------- #


def test_gate_promotes_acted_and_green():
    item = _cold_item("ok", acted_on_commit="a1b2c3d", tests_green=True)
    eligible, rejections = consolidate.gate_promotions([item])
    assert [i.slug for i in eligible] == ["ok"]
    assert rejections == []


def test_gate_rejects_null_commit():
    item = _cold_item("nocommit", acted_on_commit=None, tests_green=True)
    eligible, rejections = consolidate.gate_promotions([item])
    assert eligible == []
    assert rejections[0].reason == "ineligible: acted_on_commit=null"


def test_gate_rejects_empty_commit():
    item = _cold_item("blank", acted_on_commit="   ", tests_green=True)
    eligible, rejections = consolidate.gate_promotions([item])
    assert eligible == []
    assert "acted_on_commit=null" in rejections[0].reason


def test_gate_rejects_unknown_and_false_green():
    unk = _cold_item("unk", acted_on_commit="a1b2c3d", tests_green="unknown")
    false = _cold_item("false", acted_on_commit="a1b2c3d", tests_green=False)
    eligible, rejections = consolidate.gate_promotions([unk, false])
    assert eligible == []
    reasons = {r.target: r.reason for r in rejections}
    assert "tests_green" in reasons["unk"]
    assert "tests_green" in reasons["false"]


def test_gate_ignores_confidence_and_citations():
    # ColdItem has no confidence/citation fields; verdict depends only on the
    # consequence-veto. Two items differing in nothing the gate reads -> same.
    a = _cold_item("a", acted_on_commit="a1b2c3d", tests_green=True)
    b = _cold_item("b", acted_on_commit="a1b2c3d", tests_green=True)
    elig, rej = consolidate.gate_promotions([a, b])
    assert {i.slug for i in elig} == {"a", "b"}
    assert not hasattr(a, "confidence")
    assert not hasattr(a, "cited_sessions")


def test_is_real_sha_accepts_hex_rejects_placeholders():
    # Regression (defect 2): placeholder commit values must NOT satisfy the veto.
    assert consolidate._is_real_sha("a1b2c3d") is True
    assert consolidate._is_real_sha("0" * 40) is True
    for bad in ("TODO", "pending", "see-pr", "n/a", "sha", "sha123", "", "   ",
                "g1b2c3d", "abc", None, 123):
        assert consolidate._is_real_sha(bad) is False


def test_gate_rejects_placeholder_commit_with_green_tests():
    # An agent hand-writing a non-commit + tests_green:true must still be rejected.
    item = _cold_item("faked", acted_on_commit="see-pr", tests_green=True)
    eligible, rejections = consolidate.gate_promotions([item])
    assert eligible == []
    assert "is not a sha" in rejections[0].reason
    assert "see-pr" in rejections[0].reason


# --------------------------------------------------------------------------- #
# consolidate.decay_warm — §2.4 usage-decay + pin exemption                    #
# --------------------------------------------------------------------------- #


def test_decay_stale_warm(tmp_path):
    (tmp_path / "routes.yaml").write_text(ROUTES_YAML)
    item = _cold_item("stale", tier="warm", last_injected="2026-01-01")
    decays, keeps = consolidate.decay_warm([item], tmp_path, current_started="2026-06-03")
    assert [d.target for d in decays] == ["stale"]
    assert "warm->cold" in decays[0].reason


def test_decay_never_injected_warm(tmp_path):
    item = _cold_item("never", tier="warm", last_injected=None)
    decays, keeps = consolidate.decay_warm([item], tmp_path, current_started="2026-06-03")
    assert [d.target for d in decays] == ["never"]


def test_pinned_is_exempt_from_decay(tmp_path):
    item = _cold_item("pin", tier="warm", last_injected="2026-01-01", pinned=True)
    decays, keeps = consolidate.decay_warm([item], tmp_path, current_started="2026-06-03")
    assert decays == []
    assert keeps[0].reason == "KEPT (pinned)"


def test_recently_injected_warm_stays(tmp_path):
    item = _cold_item("fresh", tier="warm", last_injected="2026-06-03")
    decays, keeps = consolidate.decay_warm([item], tmp_path, current_started="2026-06-03")
    assert decays == []
    assert keeps and "re-injected" in keeps[0].reason


def test_decay_reads_only_last_injected(tmp_path):
    # Vary acted_on_commit/tests_green; decay verdict must not change.
    a = _cold_item("a", tier="warm", last_injected="2026-01-01",
                   acted_on_commit="sha", tests_green=True)
    b = _cold_item("b", tier="warm", last_injected="2026-01-01",
                   acted_on_commit=None, tests_green=False)
    da, _ = consolidate.decay_warm([a], tmp_path, "2026-06-03")
    db, _ = consolidate.decay_warm([b], tmp_path, "2026-06-03")
    assert [x.target for x in da] == ["a"]
    assert [x.target for x in db] == ["b"]


def test_decay_window_honors_multicampaign_threshold(tmp_path):
    # Regression (defect 3): with a campaign-start history and threshold=2, a fact
    # injected in the immediately-prior campaign is still inside the 2-campaign
    # window -> KEEP. The old code decayed any fact older than current_started.
    (tmp_path / "routes.yaml").write_text(
        "budget:\n  decay_after_campaigns: 2\n"
    )
    history = ["2026-04-01", "2026-05-01", "2026-06-01"]  # oldest..newest
    # injected during the prior (2026-05) campaign -> within last 2 windows.
    recent = _cold_item("recent", tier="warm", last_injected="2026-05-15")
    # injected two campaigns ago -> predates the 2-campaign boundary -> decay.
    old = _cold_item("old", tier="warm", last_injected="2026-04-15")
    dr, kr = consolidate.decay_warm(
        [recent], tmp_path, current_started="2026-06-01",
        campaign_starts=history,
    )
    do, ko = consolidate.decay_warm(
        [old], tmp_path, current_started="2026-06-01",
        campaign_starts=history,
    )
    assert [d.target for d in dr] == []          # kept: within window
    assert [d.target for d in do] == ["old"]     # decayed: missed 2 windows


def test_decay_falls_back_to_single_anchor_without_history(tmp_path):
    # Without start-date history the multi-campaign window cannot be honored;
    # decay is decay-after-one against current_started (documented v1 behavior).
    (tmp_path / "routes.yaml").write_text("budget:\n  decay_after_campaigns: 2\n")
    item = _cold_item("prior", tier="warm", last_injected="2026-05-15")
    decays, _ = consolidate.decay_warm(
        [item], tmp_path, current_started="2026-06-01", campaign_starts=None
    )
    assert [d.target for d in decays] == ["prior"]


def test_decay_malformed_dates_fall_back_to_keep(tmp_path):
    # Regression (defect 4 nit): a non-ISO last_injected must not raise and must
    # not spuriously decay — true date parse falls back to KEEP on parse failure.
    item = _cold_item("legacy", tier="warm", last_injected="2026/6/3")
    decays, keeps = consolidate.decay_warm(
        [item], tmp_path, current_started="2026-06-03"
    )
    assert decays == []
    assert keeps and keeps[0].target == "legacy"


def test_decay_same_day_counts_as_reexercised(tmp_path):
    # Dates compared as true date objects; same-day last_injected == current start
    # is treated as re-exercised -> KEEP (matches the prior conservative bias).
    item = _cold_item("sameday", tier="warm", last_injected="2026-06-03")
    decays, keeps = consolidate.decay_warm(
        [item], tmp_path, current_started="2026-06-03"
    )
    assert decays == []


# --------------------------------------------------------------------------- #
# distill.py — cold->warm materialization                                     #
# --------------------------------------------------------------------------- #


def test_leaf_doc_and_route_planners():
    item = _cold_item("proj", topic="projection",
                      paths=("src/platform_manifest/projection/**",))
    leaf, bullet = distill.leaf_doc_for(item, Path("/repo"))
    assert leaf == Path("/repo/docs/inject/projection.md")
    assert bullet.startswith("- ")
    entries = distill.route_entry_for(item)
    assert entries[0]["match"] == "src/platform_manifest/projection/**"
    assert entries[0]["inject"] == ["docs/inject/projection.md"]
    assert entries[0]["priority"] == distill.DEFAULT_PRIORITY


def test_materialize_writes_leaf_route_and_flips_tier(tmp_path):
    knowledge = tmp_path / ".context" / "knowledge"
    (tmp_path / ".context").mkdir(parents=True)
    (tmp_path / ".context" / "routes.yaml").write_text(ROUTES_YAML)
    item = _cold_item("proj", topic="projection",
                      paths=("src/platform_manifest/projection/**",),
                      acted_on_commit="sha", tests_green=True)
    _write_item(knowledge, item)

    distill.materialize(item, tmp_path, run_date="2026-06-03")

    leaf = tmp_path / "docs/inject/projection.md"
    assert leaf.exists()
    assert "## Inject" in leaf.read_text()
    assert item.finding in leaf.read_text()

    import yaml
    data = yaml.safe_load((tmp_path / ".context/routes.yaml").read_text())
    assert data["engine_compat"] == ">=0.2 <0.3"
    assert data["budget"]["max_docs_per_edit"] == 3
    assert any(
        r["match"] == "src/platform_manifest/projection/**"
        and "docs/inject/projection.md" in r["inject"]
        for r in data["routes"]
    )

    # cold item flipped warm + last_injected stamped; no longer cold-surfaced.
    reparsed = cold.parse_item(knowledge / "proj.md")
    assert reparsed.tier == "warm"
    assert reparsed.last_injected == "2026-06-03"
    surfaced = cold.surface_cold(
        "src/platform_manifest/projection/foo.py", knowledge, max_items=5
    )
    assert surfaced == []


def test_materialize_appends_without_clobber(tmp_path):
    (tmp_path / ".context").mkdir(parents=True)
    (tmp_path / ".context" / "routes.yaml").write_text(ROUTES_YAML)
    knowledge = tmp_path / ".context" / "knowledge"
    leaf = tmp_path / "docs/inject/projection.md"
    leaf.parent.mkdir(parents=True)
    leaf.write_text("## Inject\n- existing convention bullet\n\n## Reference\nsee cold.\n")

    item = _cold_item("proj", topic="projection",
                      paths=("src/platform_manifest/projection/**",),
                      finding="New convention from cold.",
                      acted_on_commit="sha", tests_green=True)
    _write_item(knowledge, item)
    distill.materialize(item, tmp_path, run_date="2026-06-03")

    text = leaf.read_text()
    assert "existing convention bullet" in text
    assert "New convention from cold." in text
    assert "## Reference" in text


def test_route_add_is_idempotent(tmp_path):
    (tmp_path / ".context").mkdir(parents=True)
    (tmp_path / ".context" / "routes.yaml").write_text(ROUTES_YAML)
    knowledge = tmp_path / ".context" / "knowledge"
    item = _cold_item("proj", topic="projection",
                      paths=("src/platform_manifest/projection/**",),
                      acted_on_commit="sha", tests_green=True)
    _write_item(knowledge, item)
    distill.materialize(item, tmp_path, "2026-06-03")
    # Re-run (item re-read as cold for the test) -> still exactly one route.
    distill.materialize(item, tmp_path, "2026-06-03")
    import yaml
    data = yaml.safe_load((tmp_path / ".context/routes.yaml").read_text())
    matches = [r for r in data["routes"]
               if r["match"] == "src/platform_manifest/projection/**"]
    assert len(matches) == 1


# --------------------------------------------------------------------------- #
# prune.py — §5 session pruning                                                #
# --------------------------------------------------------------------------- #


def _make_session(sessions, sid, statuses):
    active = sessions / sid / "active"
    active.mkdir(parents=True)
    for i, st in enumerate(statuses):
        (active / f"l-{i}.yaml").write_text(_capsule(st))
    return sessions / sid


def test_prune_marks_terminal_orphan(tmp_path):
    sessions = tmp_path / "sessions"
    _make_session(sessions, "s-2026-01-01-aaaa", ["done", "succeeded"])
    plans = prune.plan_prune(sessions)
    p = {pp.sid: pp for pp in plans}
    assert p["s-2026-01-01-aaaa"].prunable is True


def test_prune_keeps_active(tmp_path):
    sessions = tmp_path / "sessions"
    _make_session(sessions, "s-2026-01-01-aaaa", ["done"])
    plans = prune.plan_prune(sessions, active_sid="s-2026-01-01-aaaa")
    p = {pp.sid: pp for pp in plans}
    assert p["s-2026-01-01-aaaa"].prunable is False
    assert "active" in p["s-2026-01-01-aaaa"].reason


def test_prune_keeps_non_terminal(tmp_path):
    sessions = tmp_path / "sessions"
    _make_session(sessions, "s-2026-01-01-bbbb", ["done", "running"])
    plans = prune.plan_prune(sessions)
    p = {pp.sid: pp for pp in plans}
    assert p["s-2026-01-01-bbbb"].prunable is False


def test_prune_short_circuits_on_first_nonterminal(tmp_path, monkeypatch):
    # Regression (defect 5): a dir with a non-terminal capsule is KEEP without
    # reading every capsule. Count yaml reads via _capsule_status to prove the
    # scan stops early instead of loading all capsules.
    sessions = tmp_path / "sessions"
    active = sessions / "s-2026-01-01-big" / "active"
    active.mkdir(parents=True)
    # Every capsule is non-terminal, so whichever one iterdir yields first is a
    # KEEP signal: the scan must stop after exactly one read regardless of order.
    for i in range(200):
        (active / f"l-{i:03d}.yaml").write_text(_capsule("running"))

    reads = {"n": 0}
    real = prune._capsule_status

    def counting(path):
        reads["n"] += 1
        return real(path)

    monkeypatch.setattr(prune, "_capsule_status", counting)
    plans = prune.plan_prune(sessions)
    p = {pp.sid: pp for pp in plans}
    assert p["s-2026-01-01-big"].prunable is False
    # Stopped on the first non-terminal capsule, not after reading all 200.
    assert reads["n"] == 1


def test_prune_all_terminal_is_prunable(tmp_path):
    sessions = tmp_path / "sessions"
    _make_session(sessions, "s-2026-01-01-term", ["succeeded", "done", "failed"])
    plans = prune.plan_prune(sessions)
    p = {pp.sid: pp for pp in plans}
    assert p["s-2026-01-01-term"].prunable is True
    assert "terminal" in p["s-2026-01-01-term"].reason


def test_prune_apply_moves_to_archived(tmp_path):
    sessions = tmp_path / "sessions"
    archived = tmp_path / "archived"
    _make_session(sessions, "s-2026-01-01-aaaa", ["done"])
    _make_session(sessions, "s-2026-01-01-cccc", ["running"])
    plans = prune.plan_prune(sessions)
    moved = prune.apply_prune(plans, archived)
    assert (archived / "s-2026-01-01-aaaa") in moved
    assert (archived / "s-2026-01-01-aaaa").is_dir()
    assert not (sessions / "s-2026-01-01-aaaa").exists()
    # non-terminal stays.
    assert (sessions / "s-2026-01-01-cccc").is_dir()


def test_prune_never_lists_gitkeep(tmp_path):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / ".gitkeep").write_text("")
    plans = prune.plan_prune(sessions)
    assert all(pp.sid != ".gitkeep" for pp in plans)


# --------------------------------------------------------------------------- #
# plan_consolidation — dry-run / apply parity / never-raises                  #
# --------------------------------------------------------------------------- #


def _populate(root: Path):
    ctx = root / ".context"
    knowledge = ctx / "knowledge"
    knowledge.mkdir(parents=True)
    (ctx / "routes.yaml").write_text(ROUTES_YAML)
    (root / ".console").mkdir(parents=True)
    (root / ".console" / "task.md").write_text(TASK_WITH_FM)

    # eligible cold item.
    _write_item(knowledge, _cold_item(
        "elig", topic="elig", paths=("src/platform_manifest/e/**",),
        acted_on_commit="abc1234", tests_green=True))
    # ineligible cold item.
    _write_item(knowledge, _cold_item("inelig", topic="inelig"))
    # stale warm item.
    _write_item(knowledge, _cold_item(
        "staleware", topic="staleware", tier="warm", last_injected="2026-01-01"))
    # pinned stale warm item (exempt).
    _write_item(knowledge, _cold_item(
        "pinned", topic="pinned", tier="warm", last_injected="2026-01-01", pinned=True))

    sessions = ctx / "sessions"
    _make_session(sessions, "s-2026-01-01-orph", ["done"])
    _make_session(sessions, "s-2026-01-01-live", ["running"])


def test_dryrun_mutates_nothing(tmp_path):
    _populate(tmp_path)
    before = _snapshot(tmp_path)
    plan = consolidate.plan_consolidation(tmp_path, "c-2026-06-03-abcd", apply=False)
    after = _snapshot(tmp_path)
    assert before == after  # byte-identical

    assert {a.target for a in plan.promotions} == {"elig"}
    assert {a.target for a in plan.rejections} == {"inelig"}
    decay_targets = {a.target for a in plan.decays}
    assert "staleware" in decay_targets
    assert "pinned" not in decay_targets
    assert {a.target for a in plan.prunes} == {"s-2026-01-01-orph"}


def test_apply_matches_plan(tmp_path):
    _populate(tmp_path)
    plan = consolidate.plan_consolidation(tmp_path, "c-2026-06-03-abcd", apply=True)

    # the applied result matches the plan: exactly the eligible item promoted.
    assert len(plan.promotions) == 1

    # promoted: leaf doc + route + cold item flipped warm.
    assert (tmp_path / "docs/inject/elig.md").exists()
    elig = cold.parse_item(tmp_path / ".context/knowledge/elig.md")
    assert elig.tier == "warm"

    # decayed: stale warm demoted to cold; pinned untouched.
    assert cold.parse_item(tmp_path / ".context/knowledge/staleware.md").tier == "cold"
    assert cold.parse_item(tmp_path / ".context/knowledge/pinned.md").tier == "warm"

    # pruned orphan moved to archived; live session stays.
    assert (tmp_path / ".context/archived/s-2026-01-01-orph").is_dir()
    assert not (tmp_path / ".context/sessions/s-2026-01-01-orph").exists()
    assert (tmp_path / ".context/sessions/s-2026-01-01-live").is_dir()


def test_never_raises_on_malformed(tmp_path):
    ctx = tmp_path / ".context"
    knowledge = ctx / "knowledge"
    knowledge.mkdir(parents=True)
    (knowledge / "bad.md").write_text("not valid frontmatter at all")
    (ctx / "routes.yaml").write_text("engine_compat: '>=0.2 <0.3'\nroutes: [: bad")
    sessions = ctx / "sessions"
    sessions.mkdir()
    badsess = sessions / "s-2026-01-01-bad" / "active"
    badsess.mkdir(parents=True)
    (badsess / "l-0.yaml").write_text(":::not yaml:::")

    # No task.md at all.
    plan = consolidate.plan_consolidation(tmp_path, apply=False)
    assert isinstance(plan, consolidate.ConsolidationPlan)
    # apply on the same mess must also not raise.
    plan2 = consolidate.plan_consolidation(tmp_path, apply=True)
    assert isinstance(plan2, consolidate.ConsolidationPlan)


def test_real_repo_readonly_smoke():
    """Guard: a dry-run against the REAL repo must not raise and must not write.

    Defends the 'never mutate real .context/.console' constraint in CI.
    """
    before = _snapshot(_REPO_ROOT / ".context" / "knowledge")
    plan = consolidate.plan_consolidation(_REPO_ROOT, apply=False)
    after = _snapshot(_REPO_ROOT / ".context" / "knowledge")
    assert before == after
    assert isinstance(plan, consolidate.ConsolidationPlan)
