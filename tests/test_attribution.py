# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the D3 P2 attribution planner — the §3 red-team IS the matrix.

Every evidence seam is faked (no git, no network). Covered attacks/properties:

  - happy path: injected + cited + path-overlap + date-after + CI green
    ⇒ planned (slug, sha, True);
  - §3.1 temporal coincidence: a same-window commit WITHOUT a trailer never
    attributes (and a citing commit that PREDATES the injection is rejected
    ``cited_before_injection``);
  - §3.2 confounding: a commit citing one of two injected slugs credits only
    that one; a commit citing both but path-overlapping only one credits only
    the overlapping one (other rejected ``cited_no_path_overlap``);
  - ``never_injected``: a cited slug absent from the injection ledger can
    never be attributed;
  - §3.6 forged sha: the default git seam drops malformed records and a failed
    git exits to no evidence — only merged-default-branch listings are ever
    candidates (seam contract);
  - §3.5 racing CI: pending/absent CI ⇒ planned with verbatim ``"unknown"``,
    never True (and a nonstandard seam value degrades to ``"unknown"``);
  - §4.4 write-once: an already-attributed item is skipped;
  - §3.3 feedback loop: warm/hot cited items are out of scope (cold-only);
  - determinism: multiple qualifying commits ⇒ the earliest (author-date, then
    sha) wins;
  - fail-soft: a throwing seam ⇒ empty plan, never a raise;
  - purity: planning mutates NOTHING on disk (byte-identical tree).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from context_lifecycle.context_engine import attribution
from context_lifecycle.context_engine.attribution import (
    REASON_ALREADY_ATTRIBUTED,
    REASON_CITED_BEFORE_INJECTION,
    REASON_CITED_NO_PATH_OVERLAP,
    REASON_NEVER_INJECTED,
    REASON_NO_CITING_COMMIT,
    REASON_NOT_COLD_TIER,
    REASON_TIMESTAMP_UNPARSEABLE,
    REASON_UNKNOWN_SLUG,
    CitedCommit,
    plan_attribution,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40

# Day-scale gaps so naive-vs-offset localization can never flip an ordering.
TS_INJECT = "2026-07-10T10:00:00"
TS_BEFORE = "2026-07-01T10:00:00"
TS_AFTER = "2026-07-12T10:00:00"
TS_LATER = "2026-07-14T10:00:00"


# --------------------------------------------------------------------------- #
# fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #


def _write_item(
    root: Path,
    slug: str,
    *,
    tier: str = "cold",
    paths: tuple[str, ...] = ("src/**",),
    acted: str | None = None,
) -> None:
    knowledge = root / ".context" / "knowledge"
    knowledge.mkdir(parents=True, exist_ok=True)
    path_lines = "\n".join(f'  - "{p}"' for p in paths)
    acted_line = acted if acted is not None else "null"
    (knowledge / f"{slug}.md").write_text(
        f"""---
topic: topic of {slug}
paths:
{path_lines}
created: 2026-07-01
campaign_id: c-2026-07-01-test
consequence:
  acted_on_commit: {acted_line}
  tests_green: "unknown"
tier: {tier}
pinned: false
last_injected: null
---
## Finding
A finding for {slug}.

## Detail
"""
    )


def _write_ledger(root: Path, entries: list[tuple[str, str]]) -> None:
    """One injection event per (slug, ts) — the P0 cold_slugs record shape."""
    tel = root / ".context" / "sessions" / ".telemetry"
    tel.mkdir(parents=True, exist_ok=True)
    lines = []
    for slug, ts in entries:
        lines.append(
            json.dumps(
                {
                    "ts": ts,
                    "target": "src/x.py",
                    "injected": [],
                    "empty": [],
                    "missing": [],
                    "over_budget": [],
                    "cold_surfaced": 1,
                    "cold_slugs": [{"slug": slug, "ts": ts}],
                }
            )
        )
    (tel / "injection.jsonl").write_text("\n".join(lines) + "\n")


def _commits(*commits: CitedCommit):
    return lambda root, branch, since: list(commits)


def _files(mapping: dict[str, tuple[str, ...]]):
    return lambda root, sha: mapping.get(sha, ())


def _ci(value: object):
    return lambda repo, sha, token: value


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _plan(root: Path, **kw):
    kw.setdefault("list_cited_commits", _commits())
    kw.setdefault("changed_files", _files({}))
    kw.setdefault("resolve_tests_green", _ci("unknown"))
    return plan_attribution(root, **kw)


def _reasons(plan) -> set[tuple[str, str | None, str]]:
    return {(r.slug, r.sha, r.reason) for r in plan.rejections}


# --------------------------------------------------------------------------- #
# happy path                                                                  #
# --------------------------------------------------------------------------- #


def test_happy_path_plans_slug_sha_green(tmp_path):
    _write_item(tmp_path, "s1", paths=("src/**",))
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/mod/file.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha, a.tests_green) for a in plan.attributions] == [
        ("s1", SHA_A, True)
    ]
    assert plan.rejections == []


def test_ci_false_recorded_verbatim(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(False),
    )
    assert plan.attributions[0].tests_green is False


# --------------------------------------------------------------------------- #
# §3.1 temporal coincidence — time is only ever a negative guard              #
# --------------------------------------------------------------------------- #


def test_same_window_commit_without_trailer_attributes_nothing(tmp_path):
    # A commit landing right after the injection but citing nothing never even
    # enters the cited-commit list — co-occurrence is not causation.
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert plan.attributions == []
    assert ("s1", None, REASON_NO_CITING_COMMIT) in _reasons(plan)


def test_cited_before_injection_rejected(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_BEFORE, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert plan.attributions == []
    assert ("s1", SHA_A, REASON_CITED_BEFORE_INJECTION) in _reasons(plan)


def test_unparseable_commit_date_rejected_fail_closed(tmp_path):
    # A commit whose author-date cannot be compared can never be proven to
    # postdate the injection — reject, never guess.
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, "not-a-date", ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert plan.attributions == []
    assert ("s1", SHA_A, REASON_TIMESTAMP_UNPARSEABLE) in _reasons(plan)


def test_earliest_injection_ts_is_the_time_guard(tmp_path):
    # Injected twice; a commit after the EARLIEST injection qualifies even
    # though it predates the second injection.
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT), ("s1", TS_LATER)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_A)]


# --------------------------------------------------------------------------- #
# §3.2 confounding — citation + independent corroboration per slug            #
# --------------------------------------------------------------------------- #


def test_commit_citing_one_of_two_injected_slugs_credits_only_it(tmp_path):
    _write_item(tmp_path, "s1", paths=("src/**",))
    _write_item(tmp_path, "s2", paths=("src/**",))
    _write_ledger(tmp_path, [("s1", TS_INJECT), ("s2", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_A)]
    assert ("s2", None, REASON_NO_CITING_COMMIT) in _reasons(plan)


def test_commit_citing_both_but_overlapping_one_credits_only_it(tmp_path):
    _write_item(tmp_path, "s1", paths=("src/**",))
    _write_item(tmp_path, "s2", paths=("docs/**",))
    _write_ledger(tmp_path, [("s1", TS_INJECT), ("s2", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1", "s2"))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_A)]
    assert ("s2", SHA_A, REASON_CITED_NO_PATH_OVERLAP) in _reasons(plan)


def test_glob_matching_uses_surface_cold_semantics(tmp_path):
    # `a/**` must match the dir itself and any depth beneath it; `*` stays
    # within a segment — the exact route._glob_to_regex semantics.
    _write_item(tmp_path, "s1", paths=("src/pkg/**",))
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("./src/pkg/deep/nested/file.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_A)]

    plan2 = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/pkgs/other.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert plan2.attributions == []
    assert ("s1", SHA_A, REASON_CITED_NO_PATH_OVERLAP) in _reasons(plan2)


# --------------------------------------------------------------------------- #
# never_injected — the ledger is a hard precondition                          #
# --------------------------------------------------------------------------- #


def test_cited_but_never_injected_rejected(tmp_path):
    _write_item(tmp_path, "s1")
    _write_item(tmp_path, "s2")
    _write_ledger(tmp_path, [("s2", TS_INJECT)])  # s1 NOT in the ledger
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert plan.attributions == []
    assert ("s1", None, REASON_NEVER_INJECTED) in _reasons(plan)


def test_empty_ledger_never_queries_commits(tmp_path):
    # No injection ⇒ nothing can be attributable ⇒ the git walk is skipped.
    _write_item(tmp_path, "s1")
    calls: list[object] = []

    def seam(root, branch, since):
        calls.append(since)
        return []

    plan = _plan(tmp_path, list_cited_commits=seam)
    assert calls == []
    assert plan.attributions == []
    assert ("s1", None, REASON_NEVER_INJECTED) in _reasons(plan)


# --------------------------------------------------------------------------- #
# §3.6 forged / unresolvable sha — the seam contract                          #
# --------------------------------------------------------------------------- #


def test_default_git_seam_parses_only_wellformed_cited_records(monkeypatch):
    # Records: valid 2-slug commit; commit with NO trailer values (skipped);
    # forged non-hex "sha" (skipped); junk fragment (skipped).
    stdout = (
        f"{SHA_A}\x1f2026-07-12T10:00:00+00:00\x1fs1\x1fs2\x1e\n"
        f"{SHA_B}\x1f2026-07-12T11:00:00+00:00\x1f\x1e\n"
        "ZZZZ-not-a-sha\x1f2026-07-12T12:00:00+00:00\x1fs3\x1e\n"
        "garbage\x1e\n"
    )
    monkeypatch.setattr(
        attribution,
        "_run_git",
        lambda args, cwd, timeout=0: subprocess.CompletedProcess(
            args=["git"], returncode=0, stdout=stdout, stderr=""
        ),
    )
    commits = attribution._git_cited_commits(Path("."), "main", None)
    assert commits == [
        CitedCommit(SHA_A, "2026-07-12T10:00:00+00:00", ("s1", "s2"))
    ]


def test_default_git_seams_fail_closed_on_git_error(monkeypatch):
    monkeypatch.setattr(
        attribution,
        "_run_git",
        lambda args, cwd, timeout=0: subprocess.CompletedProcess(
            args=["git"], returncode=128, stdout="", stderr="fatal: bad ref"
        ),
    )
    assert attribution._git_cited_commits(Path("."), "main", None) == []
    assert attribution._git_changed_files(Path("."), SHA_A) == ()


def test_planner_only_sees_commits_the_merged_default_listing_returned(tmp_path):
    # Reachability is by construction: a "commit" that the default-branch
    # listing does not return (unmerged branch, forged sha) simply does not
    # exist as evidence, whatever the item or ledger claim.
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(),  # merged default branch has no citation
        changed_files=_files({SHA_C: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert plan.attributions == []
    assert ("s1", None, REASON_NO_CITING_COMMIT) in _reasons(plan)


# --------------------------------------------------------------------------- #
# §3.5 racing CI — "unknown" is a valid planned value, never True             #
# --------------------------------------------------------------------------- #


def test_pending_ci_plans_unknown(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci("unknown"),
    )
    assert [(a.slug, a.sha, a.tests_green) for a in plan.attributions] == [
        ("s1", SHA_A, "unknown")
    ]


def test_nonstandard_ci_value_degrades_to_unknown_never_true(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    for weird in ("true", 1, None, "green"):
        plan = _plan(
            tmp_path,
            list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
            changed_files=_files({SHA_A: ("src/a.py",)}),
            resolve_tests_green=_ci(weird),
        )
        assert plan.attributions[0].tests_green == "unknown"


def test_default_ci_seam_without_repo_or_token_is_unknown():
    # Caller-supplied identity: no owner/repo or no token ⇒ verbatim "unknown"
    # with no network attempt (resolve_ci_status early-returns without token).
    assert attribution._default_resolve_tests_green(None, SHA_A, "tok") == "unknown"
    assert attribution._default_resolve_tests_green("bad-shape", SHA_A, "tok") == "unknown"
    assert attribution._default_resolve_tests_green("o/r", SHA_A, None) == "unknown"


# --------------------------------------------------------------------------- #
# §4.4 write-once + §3.3 cold-only scope                                      #
# --------------------------------------------------------------------------- #


def test_already_attributed_item_skipped(tmp_path):
    _write_item(tmp_path, "s1", acted=SHA_B)
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert plan.attributions == []
    assert ("s1", None, REASON_ALREADY_ATTRIBUTED) in _reasons(plan)


def test_warm_tier_cited_item_is_out_of_scope(tmp_path):
    _write_item(tmp_path, "s1", tier="warm")
    _write_item(tmp_path, "s2", tier="cold")
    _write_ledger(tmp_path, [("s1", TS_INJECT), ("s2", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1", "s2"))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s2", SHA_A)]
    assert ("s1", SHA_A, REASON_NOT_COLD_TIER) in _reasons(plan)


def test_cited_slug_with_no_item_rejected_unknown_slug(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("ghost", "s1"))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_A)]
    assert ("ghost", SHA_A, REASON_UNKNOWN_SLUG) in _reasons(plan)


# --------------------------------------------------------------------------- #
# determinism — earliest qualifying commit wins                               #
# --------------------------------------------------------------------------- #


def test_multiple_qualifying_commits_earliest_wins(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(
            CitedCommit(SHA_B, TS_LATER, ("s1",)),
            CitedCommit(SHA_A, TS_AFTER, ("s1",)),  # earlier, listed second
        ),
        changed_files=_files({SHA_A: ("src/a.py",), SHA_B: ("src/b.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_A)]


def test_equal_author_dates_break_ties_on_sha(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(
            CitedCommit(SHA_C, TS_AFTER, ("s1",)),
            CitedCommit(SHA_A, TS_AFTER, ("s1",)),
        ),
        changed_files=_files({SHA_A: ("src/a.py",), SHA_C: ("src/c.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_A)]


def test_earliest_nonqualifying_commit_does_not_block_later_qualifier(tmp_path):
    # The earliest CITING commit fails corroboration; the later one qualifies
    # and is chosen — with the earlier rejection recorded.
    _write_item(tmp_path, "s1", paths=("src/**",))
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(
            CitedCommit(SHA_A, TS_AFTER, ("s1",)),
            CitedCommit(SHA_B, TS_LATER, ("s1",)),
        ),
        changed_files=_files({SHA_A: ("docs/no-overlap.md",), SHA_B: ("src/b.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_B)]
    assert ("s1", SHA_A, REASON_CITED_NO_PATH_OVERLAP) in _reasons(plan)


# --------------------------------------------------------------------------- #
# fail-soft + purity                                                          #
# --------------------------------------------------------------------------- #


def test_planner_never_raises_throwing_commit_seam_yields_empty_plan(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])

    def boom(root, branch, since):
        raise RuntimeError("git exploded")

    plan = _plan(tmp_path, list_cited_commits=boom)
    assert plan.is_empty()


def test_planner_never_raises_throwing_files_seam_yields_empty_plan(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])

    def boom(root, sha):
        raise OSError("no repo")

    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=boom,
    )
    assert plan.is_empty()


def test_planner_never_raises_throwing_ci_seam_yields_empty_plan(tmp_path):
    _write_item(tmp_path, "s1")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])

    def boom(repo, sha, token):
        raise ValueError("ci exploded")

    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=boom,
    )
    assert plan.is_empty()


def test_malformed_ledger_lines_are_skipped_not_fatal(tmp_path):
    _write_item(tmp_path, "s1")
    tel = tmp_path / ".context" / "sessions" / ".telemetry"
    tel.mkdir(parents=True)
    (tel / "injection.jsonl").write_text(
        "not-json\n"
        '{"cold_slugs": "not-a-list"}\n'
        '{"cold_slugs": [{"slug": "", "ts": "2026-07-10T10:00:00"}]}\n'
        '{"cold_slugs": [{"slug": "s1", "ts": "not-a-ts"}]}\n'
        + json.dumps({"cold_slugs": [{"slug": "s1", "ts": TS_INJECT}]})
        + "\n"
    )
    plan = _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert [(a.slug, a.sha) for a in plan.attributions] == [("s1", SHA_A)]


def test_planning_writes_nothing(tmp_path):
    _write_item(tmp_path, "s1")
    _write_item(tmp_path, "s2", tier="warm")
    _write_ledger(tmp_path, [("s1", TS_INJECT)])
    before = _snapshot(tmp_path)
    _plan(
        tmp_path,
        list_cited_commits=_commits(CitedCommit(SHA_A, TS_AFTER, ("s1",))),
        changed_files=_files({SHA_A: ("src/a.py",)}),
        resolve_tests_green=_ci(True),
    )
    assert _snapshot(tmp_path) == before


def test_missing_context_dir_yields_calm_empty_plan(tmp_path):
    plan = _plan(tmp_path)
    assert plan.is_empty()


# --------------------------------------------------------------------------- #
# CLI — dry-run only, exits 0, prints the plan                                #
# --------------------------------------------------------------------------- #


def test_main_dry_run_prints_and_exits_zero(tmp_path, capsys):
    assert attribution.main(["--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "attribution: nothing to attribute" in out


def test_render_lists_attributions_and_rejections():
    plan = attribution.AttributionPlan(
        attributions=[attribution.PlannedAttribution("s1", SHA_A, True)],
        rejections=[
            attribution.AttributionRejection("s2", SHA_B, REASON_CITED_NO_PATH_OVERLAP)
        ],
    )
    text = attribution._render(plan)
    assert f"ATTRIBUTE s1 -> {SHA_A} (tests_green=True)" in text
    assert f"reject s2 @ {SHA_B} ({REASON_CITED_NO_PATH_OVERLAP})" in text
