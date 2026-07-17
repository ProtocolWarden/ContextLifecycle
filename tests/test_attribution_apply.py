# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the D3 P3 two-phase consequence writer (attribution_apply).

Every seam is faked (no git, no gh, no network). Covered:

  - Phase A happy path: frontmatter after == planned values VERBATIM (True,
    False, and a planned "unknown" written as "unknown" — never upgraded;
    nonstandard planned values degrade to "unknown", never True);
  - write-once RE-CHECKED at write time (stale plan): item attributed on disk
    between plan and apply ⇒ skipped; item promoted to warm ⇒ skipped; item
    deleted ⇒ skipped;
  - Phase B: "unknown" -> True and "unknown" -> False flips; unresolved /
    nonstandard seam values leave "unknown"; provider (token/repo) failure ⇒
    Phase B no-op (safe-inert);
  - MONOTONE guards: True stays True, False stays False, no re-flip — even
    when the seam contradicts the resolved value;
  - per-item isolation: a seam raising mid-batch records that item and the
    rest still process; a write failure is recorded, never raised;
  - run_attribution: dry-run is byte-pure and carries plan + pending flips;
    plan failure degrades to an empty outcome;
  - consolidate integration: dry-run RENDERS the attribution plan + pending
    Phase-B flips with zero writes; a failing runner never breaks the
    consolidation output; and apply-then-gate — a Phase-A True write and a
    Phase-B flip each pass gate_promotions in the SAME
    plan_consolidation(apply=True) pass.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from context_lifecycle.context_engine import attribution_apply, cold, consolidate
from context_lifecycle.context_engine.attribution import (
    AttributionPlan,
    PlannedAttribution,
)
from context_lifecycle.context_engine.attribution_apply import (
    SKIP_ALREADY_ATTRIBUTED,
    SKIP_CI_UNRESOLVED,
    SKIP_FLIP_FAILED,
    SKIP_ITEM_MISSING,
    SKIP_NOT_COLD_TIER,
    SKIP_WRITE_FAILED,
    AttributionOutcome,
    PendingFlip,
    apply_attribution,
    pending_flips,
    run_attribution,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40

ROUTES_YAML = """engine_compat: ">=0.2 <0.3"
budget:
  max_docs_per_edit: 3
routes:
  - match: "src/platform_manifest/loader.py"
    inject: ["docs/inject/loader.md"]
    priority: 10
"""


# --------------------------------------------------------------------------- #
# fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


def _item(slug: str, **over) -> cold.ColdItem:
    base = dict(
        topic=over.pop("topic", slug),
        paths=("src/**",),
        created="2026-07-01",
        campaign_id="c-2026-07-01-test",
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


def _put(root: Path, slug: str, **over) -> Path:
    return cold.write_item(root / ".context" / "knowledge", _item(slug, **over))


def _get(root: Path, slug: str) -> cold.ColdItem | None:
    return cold.parse_item(root / ".context" / "knowledge" / f"{slug}.md")


def _plan(*attributions: PlannedAttribution) -> AttributionPlan:
    return AttributionPlan(attributions=list(attributions))


def _ci(value: object):
    return lambda repo, sha, token: value


def _apply(root: Path, plan=None, *, ci: object = "unknown", **kw):
    kw.setdefault("github_repo", "o/r")
    kw.setdefault("token", "t")
    kw.setdefault("resolve_tests_green", ci if callable(ci) else _ci(ci))
    return apply_attribution(root, plan if plan is not None else _plan(), **kw)


def _skips(result) -> set[tuple[str, str]]:
    return {(s.slug, s.reason) for s in result.skipped}


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# --------------------------------------------------------------------------- #
# Phase A — verbatim writes, re-checked write-once                            #
# --------------------------------------------------------------------------- #


def test_phase_a_writes_planned_true_verbatim(tmp_path):
    _put(tmp_path, "s1")
    result = _apply(tmp_path, _plan(PlannedAttribution("s1", SHA_A, True)))
    assert [(w.slug, w.sha, w.tests_green) for w in result.applied] == [
        ("s1", SHA_A, True)
    ]
    item = _get(tmp_path, "s1")
    assert item.acted_on_commit == SHA_A
    assert item.tests_green is True
    text = (tmp_path / ".context/knowledge/s1.md").read_text(encoding="utf-8")
    assert f"acted_on_commit: {SHA_A}" in text
    assert "tests_green: true" in text


def test_phase_a_writes_planned_false_verbatim(tmp_path):
    _put(tmp_path, "s1")
    result = _apply(tmp_path, _plan(PlannedAttribution("s1", SHA_A, False)))
    assert result.applied[0].tests_green is False
    assert _get(tmp_path, "s1").tests_green is False


def test_phase_a_planned_unknown_written_as_unknown_never_upgraded(tmp_path):
    _put(tmp_path, "s1")
    result = _apply(
        tmp_path, _plan(PlannedAttribution("s1", SHA_A, "unknown")), ci="unknown"
    )
    assert result.applied[0].tests_green == "unknown"
    item = _get(tmp_path, "s1")
    assert item.acted_on_commit == SHA_A
    assert item.tests_green == "unknown"
    text = (tmp_path / ".context/knowledge/s1.md").read_text(encoding="utf-8")
    assert "tests_green: unknown" in text


def test_phase_a_nonstandard_planned_value_degrades_to_unknown(tmp_path):
    for i, weird in enumerate(("true", 1, None, "green")):
        slug = f"s{i}"
        _put(tmp_path, slug)
        result = _apply(
            tmp_path, _plan(PlannedAttribution(slug, SHA_A, weird)), ci="unknown"
        )
        assert result.applied[-1].tests_green == "unknown"
        assert _get(tmp_path, slug).tests_green == "unknown"


def test_stale_plan_item_attributed_on_disk_is_skipped(tmp_path):
    # Attributed (to a DIFFERENT sha) between plan time and apply time:
    # write-once is re-checked at write time — first attribution wins.
    _put(tmp_path, "s1", acted_on_commit=SHA_B)
    before = _snapshot(tmp_path)
    result = _apply(tmp_path, _plan(PlannedAttribution("s1", SHA_A, True)))
    assert result.applied == []
    assert ("s1", SKIP_ALREADY_ATTRIBUTED) in _skips(result)
    assert _get(tmp_path, "s1").acted_on_commit == SHA_B
    assert _snapshot(tmp_path) == before


def test_stale_plan_item_promoted_to_warm_is_skipped(tmp_path):
    _put(tmp_path, "s1", tier="warm")
    before = _snapshot(tmp_path)
    result = _apply(tmp_path, _plan(PlannedAttribution("s1", SHA_A, True)))
    assert result.applied == []
    assert ("s1", SKIP_NOT_COLD_TIER) in _skips(result)
    assert _snapshot(tmp_path) == before


def test_stale_plan_item_deleted_is_skipped(tmp_path):
    (tmp_path / ".context" / "knowledge").mkdir(parents=True)
    result = _apply(tmp_path, _plan(PlannedAttribution("gone", SHA_A, True)))
    assert result.applied == []
    assert ("gone", SKIP_ITEM_MISSING) in _skips(result)


def test_phase_a_write_failure_recorded_never_raised(tmp_path):
    p1 = _put(tmp_path, "s1")
    _put(tmp_path, "s2")
    p1.chmod(0o444)  # reads fine, the whole-file rewrite fails
    try:
        result = _apply(
            tmp_path,
            _plan(
                PlannedAttribution("s1", SHA_A, True),
                PlannedAttribution("s2", SHA_B, True),
            ),
        )
    finally:
        p1.chmod(0o644)
    # s1's failure is recorded; the batch continued and s2 still applied.
    assert ("s1", SKIP_WRITE_FAILED) in _skips(result)
    assert [(w.slug, w.sha) for w in result.applied] == [("s2", SHA_B)]
    assert _get(tmp_path, "s1").acted_on_commit is None
    assert _get(tmp_path, "s2").acted_on_commit == SHA_B


# --------------------------------------------------------------------------- #
# Phase B — CI flips, fail-closed and monotone                                #
# --------------------------------------------------------------------------- #


def test_phase_b_flips_unknown_to_true(tmp_path):
    _put(tmp_path, "s1", acted_on_commit=SHA_A)
    result = _apply(tmp_path, ci=True)
    assert [(w.slug, w.sha, w.tests_green) for w in result.flipped] == [
        ("s1", SHA_A, True)
    ]
    assert _get(tmp_path, "s1").tests_green is True


def test_phase_b_flips_unknown_to_false(tmp_path):
    _put(tmp_path, "s1", acted_on_commit=SHA_A)
    result = _apply(tmp_path, ci=False)
    assert result.flipped[0].tests_green is False
    assert _get(tmp_path, "s1").tests_green is False


def test_phase_b_unresolved_leaves_unknown(tmp_path):
    _put(tmp_path, "s1", acted_on_commit=SHA_A)
    before = _snapshot(tmp_path)
    result = _apply(tmp_path, ci="unknown")
    assert result.flipped == []
    assert ("s1", SKIP_CI_UNRESOLVED) in _skips(result)
    assert _snapshot(tmp_path) == before


def test_phase_b_nonstandard_seam_value_never_written(tmp_path):
    # Only LITERAL True/False are ever written — truthy imposters are not.
    _put(tmp_path, "s1", acted_on_commit=SHA_A)
    before = _snapshot(tmp_path)
    for weird in ("true", 1, None, "green", "false", 0):
        result = _apply(tmp_path, ci=weird)
        assert result.flipped == []
        assert ("s1", SKIP_CI_UNRESOLVED) in _skips(result)
    assert _snapshot(tmp_path) == before


def test_monotone_true_stays_true(tmp_path):
    _put(tmp_path, "s1", acted_on_commit=SHA_A, tests_green=True)
    before = _snapshot(tmp_path)
    result = _apply(tmp_path, ci=False)  # a contradicting seam changes nothing
    assert result.flipped == [] and result.applied == [] and result.skipped == []
    assert _snapshot(tmp_path) == before


def test_monotone_false_stays_false(tmp_path):
    _put(tmp_path, "s1", acted_on_commit=SHA_A, tests_green=False)
    before = _snapshot(tmp_path)
    result = _apply(tmp_path, ci=True)
    assert result.flipped == []
    assert _snapshot(tmp_path) == before


def test_monotone_no_reflip_across_passes(tmp_path):
    # "unknown" -> True|False happens AT MOST ONCE: a second pass with a
    # contradicting seam is a no-op.
    _put(tmp_path, "s1", acted_on_commit=SHA_A)
    assert _apply(tmp_path, ci=True).flipped != []
    after_flip = _snapshot(tmp_path)
    result = _apply(tmp_path, ci=False)
    assert result.flipped == []
    assert _snapshot(tmp_path) == after_flip
    assert _get(tmp_path, "s1").tests_green is True


def test_phase_b_seam_raising_mid_batch_isolated(tmp_path):
    _put(tmp_path, "s1", acted_on_commit=SHA_A)
    _put(tmp_path, "s2", acted_on_commit=SHA_B)

    def seam(repo, sha, token):
        if sha == SHA_A:
            raise RuntimeError("ci exploded")
        return True

    result = _apply(tmp_path, ci=seam)
    assert ("s1", SKIP_FLIP_FAILED) in _skips(result)
    assert [(w.slug, w.tests_green) for w in result.flipped] == [("s2", True)]
    assert _get(tmp_path, "s1").tests_green == "unknown"
    assert _get(tmp_path, "s2").tests_green is True


def test_provider_failures_leave_phase_b_inert(tmp_path):
    # No token / no repo identity ⇒ the default fail-closed resolver answers
    # "unknown" without any network attempt ⇒ nothing flips (safe-inert).
    _put(tmp_path, "s1", acted_on_commit=SHA_A)
    before = _snapshot(tmp_path)
    for repo_p, token_p in (
        (lambda root: None, lambda: None),
        (lambda root: (_ for _ in ()).throw(OSError("no git")), lambda: "t"),
        (lambda root: "o/r", lambda: (_ for _ in ()).throw(OSError("no gh"))),
    ):
        result = apply_attribution(
            tmp_path,
            _plan(),
            repo_provider=repo_p,
            token_provider=token_p,
        )
        assert result.flipped == []
        assert ("s1", SKIP_CI_UNRESOLVED) in _skips(result)
    assert _snapshot(tmp_path) == before


def test_providers_not_consulted_without_pending_flips(tmp_path):
    _put(tmp_path, "s1")  # unattributed: Phase B worklist is empty
    calls: list[str] = []
    result = apply_attribution(
        tmp_path,
        _plan(),
        repo_provider=lambda root: calls.append("repo"),
        token_provider=lambda: calls.append("token"),
    )
    assert calls == []
    assert result.is_empty()


def test_phase_a_fresh_unknown_is_phase_b_eligible_same_pass(tmp_path):
    # A Phase-A write of sha+"unknown" whose CI verdict is ALREADY resolvable
    # flips in the same apply pass — an immediate §3.5 "arrival".
    _put(tmp_path, "s1")
    result = _apply(
        tmp_path, _plan(PlannedAttribution("s1", SHA_A, "unknown")), ci=True
    )
    assert result.applied[0].tests_green == "unknown"  # Phase A verbatim
    assert result.flipped[0].tests_green is True  # Phase B flip
    assert _get(tmp_path, "s1").tests_green is True


def test_pending_flips_lists_only_attributed_unknown_cold_items(tmp_path):
    _put(tmp_path, "pend", acted_on_commit=SHA_A)
    _put(tmp_path, "unattributed")
    _put(tmp_path, "resolved", acted_on_commit=SHA_B, tests_green=True)
    _put(tmp_path, "warm", tier="warm", acted_on_commit=SHA_C)
    _put(tmp_path, "fake", acted_on_commit="see-pr")
    assert pending_flips(tmp_path) == [PendingFlip("pend", SHA_A)]
    assert pending_flips(tmp_path / "nowhere") == []


# --------------------------------------------------------------------------- #
# default identity providers (subprocess seams faked)                         #
# --------------------------------------------------------------------------- #


def test_parse_remote_forms():
    for url in (
        "https://github.com/o/r",
        "https://github.com/o/r.git",
        "https://github.com/o/r/",
        "ssh://git@github.com/o/r.git",
        "git@github.com:o/r.git",
        "git@github.com:o/r",
    ):
        assert attribution_apply._parse_remote(url) == "o/r"
    for bad in ("", "not a url", "https://github.com/only-owner", "o/r"):
        assert attribution_apply._parse_remote(bad) is None


def _completed(returncode: int, stdout: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=""
    )


def test_default_token_provider_success_and_failure(monkeypatch):
    monkeypatch.setattr(
        attribution_apply.subprocess, "run", lambda *a, **k: _completed(0, "tok\n")
    )
    assert attribution_apply._default_token_provider() == "tok"
    monkeypatch.setattr(
        attribution_apply.subprocess, "run", lambda *a, **k: _completed(1, "")
    )
    assert attribution_apply._default_token_provider() is None

    def boom(*a, **k):
        raise FileNotFoundError("gh not installed")

    monkeypatch.setattr(attribution_apply.subprocess, "run", boom)
    assert attribution_apply._default_token_provider() is None


def test_default_repo_provider_success_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        attribution_apply.subprocess,
        "run",
        lambda *a, **k: _completed(0, "git@github.com:o/r.git\n"),
    )
    assert attribution_apply._default_repo_provider(tmp_path) == "o/r"
    monkeypatch.setattr(
        attribution_apply.subprocess,
        "run",
        lambda *a, **k: _completed(128, ""),
    )
    assert attribution_apply._default_repo_provider(tmp_path) is None


# --------------------------------------------------------------------------- #
# run_attribution — the consolidate entrypoint                                #
# --------------------------------------------------------------------------- #


def test_run_attribution_dry_run_is_pure_and_visible(tmp_path):
    _put(tmp_path, "s1")
    _put(tmp_path, "pend", acted_on_commit=SHA_B)
    plan = _plan(PlannedAttribution("s1", SHA_A, "unknown"))
    before = _snapshot(tmp_path)
    outcome = run_attribution(tmp_path, apply=False, plan_fn=lambda r: plan)
    assert _snapshot(tmp_path) == before  # byte-identical: ZERO writes
    assert outcome.plan is plan
    assert outcome.applied is None
    assert outcome.pending == [PendingFlip("pend", SHA_B)]


def test_run_attribution_apply_dispatches_and_reports_remaining(tmp_path):
    _put(tmp_path, "s1")
    _put(tmp_path, "pend", acted_on_commit=SHA_B)
    plan = _plan(PlannedAttribution("s1", SHA_A, "unknown"))
    outcome = run_attribution(
        tmp_path,
        apply=True,
        plan_fn=lambda r: plan,
        apply_fn=lambda r, p: apply_attribution(
            r, p, github_repo="o/r", token="t", resolve_tests_green=_ci("unknown")
        ),
    )
    assert [(w.slug, w.sha) for w in outcome.applied.applied] == [("s1", SHA_A)]
    # Nothing resolved, so BOTH attributed items remain pending on disk.
    assert set(outcome.pending) == {
        PendingFlip("pend", SHA_B),
        PendingFlip("s1", SHA_A),
    }


def test_run_attribution_plan_failure_degrades_no_apply(tmp_path):
    _put(tmp_path, "pend", acted_on_commit=SHA_B)

    def boom(root):
        raise RuntimeError("planner exploded")

    before = _snapshot(tmp_path)
    outcome = run_attribution(
        tmp_path,
        apply=True,
        plan_fn=boom,
        apply_fn=lambda r, p: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert outcome.plan is None
    assert outcome.applied is None
    assert outcome.pending == [PendingFlip("pend", SHA_B)]
    assert _snapshot(tmp_path) == before


def test_render_outcome_shows_plan_pending_and_writes():
    outcome = AttributionOutcome(
        plan=_plan(PlannedAttribution("s1", SHA_A, "unknown")),
        applied=attribution_apply.AppliedResult(
            applied=[attribution_apply.AppliedWrite("s1", SHA_A, "unknown")],
            flipped=[attribution_apply.AppliedWrite("s2", SHA_B, True)],
            skipped=[attribution_apply.SkippedWrite("s3", SKIP_CI_UNRESOLVED)],
        ),
        pending=[PendingFlip("s3", SHA_C)],
    )
    text = attribution_apply.render_outcome(outcome)
    assert f"ATTRIBUTE s1 -> {SHA_A} (tests_green='unknown')" in text
    assert f"PENDING-CI-FLIP s3 @ {SHA_C}" in text
    assert f"ATTRIBUTED s1 -> {SHA_A} (tests_green='unknown')" in text
    assert f"FLIPPED s2 tests_green=True @ {SHA_B}" in text
    assert f"skip s3 ({SKIP_CI_UNRESOLVED})" in text
    assert attribution_apply.render_outcome(AttributionOutcome()) == ""


# --------------------------------------------------------------------------- #
# consolidate integration — inside the reviewed --apply boundary              #
# --------------------------------------------------------------------------- #


def _populate(root: Path) -> None:
    ctx = root / ".context"
    (ctx / "knowledge").mkdir(parents=True)
    (ctx / "routes.yaml").write_text(ROUTES_YAML, encoding="utf-8")
    (root / ".console").mkdir(parents=True)
    (root / ".console" / "task.md").write_text(
        "---\ncampaign_id: c-2026-07-01-test\nstatus: active\n"
        "started: 2026-07-01\n---\n\n# Task\n\n## Objective\n\nD3 P3.\n",
        encoding="utf-8",
    )


def _runner(plan: AttributionPlan, ci: object):
    def runner(root: Path, apply: bool):
        return run_attribution(
            root,
            apply=apply,
            plan_fn=lambda r: plan,
            apply_fn=lambda r, p: apply_attribution(
                r, p, github_repo="o/r", token="t", resolve_tests_green=_ci(ci)
            ),
        )

    return runner


def test_consolidate_dry_run_renders_attribution_with_zero_writes(tmp_path):
    _populate(tmp_path)
    _put(tmp_path, "s1")
    _put(tmp_path, "pend", acted_on_commit=SHA_B)
    plan = _plan(PlannedAttribution("s1", SHA_A, "unknown"))
    before = _snapshot(tmp_path)
    cplan = consolidate.plan_consolidation(
        tmp_path, apply=False, attribution_runner=_runner(plan, True)
    )
    assert _snapshot(tmp_path) == before  # byte-identical tree
    text = consolidate._render(cplan)
    assert f"ATTRIBUTE s1 -> {SHA_A} (tests_green='unknown')" in text
    assert f"PENDING-CI-FLIP pend @ {SHA_B}" in text
    # The existing consolidation output is intact alongside.
    assert "reject" in text


def test_consolidate_attribution_failure_never_breaks_dry_run(tmp_path):
    _populate(tmp_path)
    _put(tmp_path, "s1")

    def boom(root, apply):
        raise RuntimeError("attribution exploded")

    cplan = consolidate.plan_consolidation(
        tmp_path, apply=False, attribution_runner=boom
    )
    assert cplan.attribution is None
    assert {a.target for a in cplan.rejections} == {"s1"}
    assert "reject s1" in consolidate._render(cplan)


def test_consolidate_without_runner_is_unchanged(tmp_path):
    _populate(tmp_path)
    _put(tmp_path, "s1")
    cplan = consolidate.plan_consolidation(tmp_path, apply=False)
    assert cplan.attribution is None


def test_apply_then_gate_phase_a_true_promotes_same_pass(tmp_path):
    # The keystone: a Phase-A (sha, True) write makes the item pass
    # gate_promotions inside the SAME plan_consolidation(apply=True) pass.
    _populate(tmp_path)
    _put(tmp_path, "s1")
    plan = _plan(PlannedAttribution("s1", SHA_A, True))
    cplan = consolidate.plan_consolidation(
        tmp_path, apply=True, attribution_runner=_runner(plan, "unknown")
    )
    assert {a.target for a in cplan.promotions} == {"s1"}
    assert (tmp_path / "docs/inject/s1.md").exists()  # materialized
    item = _get(tmp_path, "s1")
    assert item.tier == "warm"
    assert item.acted_on_commit == SHA_A
    assert item.tests_green is True


def test_apply_then_gate_phase_b_flip_promotes_same_pass(tmp_path):
    # An item attributed in an EARLIER pass ("unknown") whose CI has since
    # resolved green: Phase B flips it and the gate promotes it, same pass.
    _populate(tmp_path)
    _put(tmp_path, "s1", acted_on_commit=SHA_A)
    cplan = consolidate.plan_consolidation(
        tmp_path, apply=True, attribution_runner=_runner(_plan(), True)
    )
    assert {a.target for a in cplan.promotions} == {"s1"}
    assert _get(tmp_path, "s1").tier == "warm"
    assert _get(tmp_path, "s1").tests_green is True


def test_consolidate_cli_default_runner_dry_run_never_breaks(tmp_path, capsys):
    # The CLI wires the default runner; on an empty root (no git, no items)
    # the whole attribution path degrades silently and exit stays 0.
    assert consolidate.main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "consolidate: nothing to do" in out
