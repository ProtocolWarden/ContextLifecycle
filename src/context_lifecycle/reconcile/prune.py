# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl reconcile prune` — move-and-trim completed history (spec §3.3).

Runs only when ``cl reconcile check`` is green. For each ``done`` item with
``owner == repo`` it:

1. MOVES matching ``## `` log sections (and completed ``## Done`` backlog
   content) to ``<private>/archive/console/<repo>/<file>-<cutoff>.md`` (append;
   create dirs) — never deletes (I3).
2. Trims the tracked source to active sections + most-recent-N (default 10) log
   entries + a one-line pointer to the archive.
3. Appends a scrubbed one-line entry to the repo's ``CHANGELOG.md``.

Idempotent (a re-run after a green prune is a no-op). Dry-run by default;
``--apply`` mutates. Refuses to run if ``check`` is not green.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from context_lifecycle.reconcile.check import CheckResult, run_check
from context_lifecycle.reconcile.mdsections import Section, join_sections, split_sections
from context_lifecycle.reconcile.privacy import archive_dir_for, is_private_root
from context_lifecycle.reconcile.scrub import ScrubVocabulary, load_scrub_vocabulary
from context_lifecycle.reconcile.worksheet import ReconcileItem, Worksheet, load_worksheet

LOG_RELPATH = Path(".console") / "log.md"
BACKLOG_RELPATH = Path(".console") / "backlog.md"
CHANGELOG_RELPATH = Path("CHANGELOG.md")
DEFAULT_RECENT_N = 10

# Backlog sections that stay active (never archived).
_ACTIVE_BACKLOG = {"in progress", "up next"}
_POINTER_PREFIX = "_Archived completed history →"
_BACKLOG_ARCHIVED_MARKER = "_Completed items archived._"

_TOKEN = re.compile(r"[A-Za-z0-9]+")


class PruneRefused(RuntimeError):
    """Raised when prune is invoked but the I1 gate is not green."""


@dataclass
class PlannedMove:
    source: str          # "log.md" | "backlog.md"
    heading: str
    matched_item: str    # item id that claimed this section


@dataclass
class PrunePlan:
    repo: str
    cutoff: str
    archive_dir: Path
    moves: list[PlannedMove] = field(default_factory=list)
    changelog_lines: list[str] = field(default_factory=list)
    applied: bool = False
    noop: bool = False
    messages: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return not self.moves


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN.findall(text)}


def _item_keywords(item: ReconcileItem) -> set[str]:
    """Significant tokens identifying an item's history (id + title words)."""
    kws = _tokens(item.id) | _tokens(item.title)
    # Drop trivial stop-words that would over-match.
    return {k for k in kws if len(k) > 2 and k not in {"the", "and", "for", "with"}}


def _section_matches_item(section: Section, keywords: set[str]) -> bool:
    if not keywords:
        return False
    heading_tokens = _tokens(section.title)
    return bool(keywords & heading_tokens)


def _scrub_line(text: str, vocab: ScrubVocabulary) -> str:
    """Genericize any scrub-target name in a one-line public record."""
    return vocab.redact(text, "a private downstream repo")


def _changelog_entry(item: ReconcileItem, cutoff: str, vocab: ScrubVocabulary, *, scrub: bool = True) -> str:
    raw = f"- {cutoff}: reconciled `{item.id}` — {item.title} (history archived)."
    return _scrub_line(raw, vocab) if scrub else raw


def _repo_is_private(repo: str, vocab: ScrubVocabulary, repo_root: Path | None = None) -> bool:
    """A repo whose own name is a scrub target — or which IS the
    private-manifest root — is a private repo.

    Inside a private repo, private names are not leaks (the boundary protects
    public surfaces) — so genericizing its CHANGELOG lines or retained
    ``.console/`` content would destroy meaning, not protect anything.
    """
    if vocab.matches(repo):
        return True
    return repo_root is not None and is_private_root(repo_root)


def build_plan(
    repo_root: Path,
    *,
    recent_n: int = DEFAULT_RECENT_N,
    cutoff: str | None = None,
    vocab: ScrubVocabulary | None = None,
    worksheet: Worksheet | None = None,
    check: CheckResult | None = None,
    private_root: Path | None = None,
) -> PrunePlan:
    """Plan a prune (no mutation). Raises PruneRefused if the gate isn't green."""
    repo_root = Path(repo_root)
    vocabulary = vocab if vocab is not None else load_scrub_vocabulary()
    ws = worksheet if worksheet is not None else load_worksheet(repo_root)
    chk = check if check is not None else run_check(repo_root, vocab=vocabulary, worksheet=ws)
    if not chk.green:
        raise PruneRefused(
            f"refusing to prune {ws.repo}: check is not green "
            f"({len(chk.doc_gaps)} doc-gap(s), {len(chk.scrub_hits)} scrub leak(s))"
        )

    cutoff = cutoff or date.today().isoformat()
    archive_dir = archive_dir_for(ws.repo, private_root=private_root)
    plan = PrunePlan(repo=ws.repo, cutoff=cutoff, archive_dir=archive_dir)
    scrub_records = not _repo_is_private(ws.repo, vocabulary, repo_root)

    owned_done = [it for it in ws.items if it.is_done and not it.is_cross_repo(ws.repo)]

    # --- log.md: match sections by item keywords -------------------------
    log_path = repo_root / LOG_RELPATH
    if log_path.is_file():
        _, sections = split_sections(log_path.read_text(encoding="utf-8"))
        claimed: set[int] = set()
        for item in owned_done:
            kws = _item_keywords(item)
            item_claimed = False
            for idx, sec in enumerate(sections):
                if idx in claimed:
                    continue
                if sec.heading and _section_matches_item(sec, kws):
                    claimed.add(idx)
                    plan.moves.append(PlannedMove("log.md", sec.heading, item.id))
                    item_claimed = True
            # One CHANGELOG line per ITEM (not per matched section) — an item
            # whose keywords match several log sections still ships one entry.
            if item_claimed:
                plan.changelog_lines.append(
                    _changelog_entry(item, cutoff, vocabulary, scrub=scrub_records)
                )

    # --- backlog.md: completed ("Done"/"Done (...)") sections are
    # archive-eligible (active In Progress / Up Next / Recent stay) ------
    backlog_path = repo_root / BACKLOG_RELPATH
    if backlog_path.is_file() and owned_done:
        _, sections = split_sections(backlog_path.read_text(encoding="utf-8"))
        for sec in sections:
            if _is_completed_backlog_section(sec):
                plan.moves.append(PlannedMove("backlog.md", sec.heading, "(backlog-done)"))

    if not plan.moves:
        plan.noop = True
        plan.messages.append(f"prune: nothing to archive for {ws.repo} (already reconciled).")
    return plan


def _retain_recent_log(sections: list[Section], claimed_headings: set[str], recent_n: int) -> tuple[list[Section], list[Section]]:
    """Split log sections into (kept, archived).

    Archives claimed sections AND any non-claimed sections beyond the most-recent
    ``recent_n`` (log entries are newest-first by convention). Returns sections in
    original order.
    """
    kept: list[Section] = []
    archived: list[Section] = []
    unclaimed_seen = 0
    for sec in sections:
        if sec.heading in claimed_headings:
            archived.append(sec)
            continue
        unclaimed_seen += 1
        if unclaimed_seen <= recent_n:
            kept.append(sec)
        else:
            archived.append(sec)
    return kept, archived


def apply_plan(
    repo_root: Path,
    plan: PrunePlan,
    *,
    recent_n: int = DEFAULT_RECENT_N,
    vocab: ScrubVocabulary | None = None,
) -> PrunePlan:
    """Execute ``plan``: append to archive, trim source, append CHANGELOG. Idempotent.

    The content that *remains* tracked-public is scrubbed of scrub-target names
    (genericized) as a final step so the post-prune tracked ``.console/`` is
    R2-clean (spec §5.3 / AC10). Scrubbing already-clean text is a no-op, so a
    second ``--apply`` stays idempotent.
    """
    repo_root = Path(repo_root)
    vocabulary = vocab if vocab is not None else load_scrub_vocabulary()
    # Private repos keep their own names — scrubbing retained content there
    # would destroy meaning (see _repo_is_private).
    scrub_retained = not _repo_is_private(plan.repo, vocabulary, repo_root)
    if plan.is_noop:
        # Even with nothing to archive, ensure retained content is R2-clean.
        if scrub_retained:
            _scrub_retained(repo_root, vocabulary)
        plan.applied = True
        return plan

    plan.archive_dir.mkdir(parents=True, exist_ok=True)
    claimed_log = {m.heading for m in plan.moves if m.source == "log.md"}

    # --- log.md ----------------------------------------------------------
    log_path = repo_root / LOG_RELPATH
    if log_path.is_file():
        preamble, sections = split_sections(log_path.read_text(encoding="utf-8"))
        kept, archived = _retain_recent_log(sections, claimed_log, recent_n)
        if archived:
            archive_file = plan.archive_dir / f"log-{plan.cutoff}.md"
            _append_archive(archive_file, plan.repo, "log.md", plan.cutoff, archived)
            pointer = _pointer_section(archive_file)
            kept = _ensure_pointer(kept, pointer)
            log_path.write_text(join_sections(preamble, kept), encoding="utf-8")

    # --- backlog.md ------------------------------------------------------
    backlog_path = repo_root / BACKLOG_RELPATH
    if backlog_path.is_file() and any(m.source == "backlog.md" for m in plan.moves):
        preamble, sections = split_sections(backlog_path.read_text(encoding="utf-8"))
        kept_b: list[Section] = []
        archived_b: list[Section] = []
        for sec in sections:
            if _is_completed_backlog_section(sec):
                archived_b.append(sec)
                # Replace the completed body with an empty header + pointer note.
                kept_b.append(Section(heading=sec.heading, body=f"## {sec.heading}\n\n_Completed items archived._\n\n"))
            else:
                kept_b.append(sec)
        if archived_b:
            archive_file = plan.archive_dir / f"backlog-{plan.cutoff}.md"
            _append_archive(archive_file, plan.repo, "backlog.md", plan.cutoff, archived_b)
            backlog_path.write_text(join_sections(preamble, kept_b), encoding="utf-8")

    # --- CHANGELOG.md ----------------------------------------------------
    if plan.changelog_lines:
        _append_changelog(repo_root / CHANGELOG_RELPATH, plan.changelog_lines)

    # --- scrub retained tracked content (R2-clean; AC10; public repos) ---
    if scrub_retained:
        _scrub_retained(repo_root, vocabulary)

    plan.applied = True
    return plan


def _is_completed_backlog_section(sec: Section) -> bool:
    """True for a backlog section holding completed work (archive-eligible).

    A bare ``Done`` heading, or any ``Done (...)`` variant, or a section whose
    body carries a completed-item line (``[x]``) and is not an active section
    (``In Progress`` / ``Up Next`` / ``Recent``).
    """
    if not sec.heading:
        return False
    # Already pruned (a previous --apply emptied it) → no-op on re-run (AC9).
    if _BACKLOG_ARCHIVED_MARKER in sec.body:
        return False
    title = sec.heading.strip().lower()
    if title in _ACTIVE_BACKLOG or title == "recent":
        return False
    if title == "done" or title.startswith("done "):
        return True
    return "[x]" in sec.body.lower()


def _scrub_retained(repo_root: Path, vocab: ScrubVocabulary) -> None:
    """Genericize any scrub-target name left in the tracked ``.console/`` sources.

    Idempotent: re-running on already-clean text changes nothing. Skips when the
    vocabulary is empty (no source configured) so behaviour is unchanged in that
    case. The archive (private side) is never touched — names are allowed there.
    """
    if vocab.is_empty():
        return
    for rel in (LOG_RELPATH, BACKLOG_RELPATH):
        path = repo_root / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        scrubbed = vocab.redact(text, "a private downstream repo")
        if scrubbed != text:
            path.write_text(scrubbed, encoding="utf-8")


def _pointer_section(archive_file: Path) -> Section:
    heading = "Archived"
    body = f"## {heading}\n\n{_POINTER_PREFIX} `{archive_file}`_\n\n"
    return Section(heading=heading, body=body)


def _ensure_pointer(sections: list[Section], pointer: Section) -> list[Section]:
    """Append the pointer section unless one already references the same archive."""
    for sec in sections:
        if sec.heading == pointer.heading and _POINTER_PREFIX in sec.body:
            return sections  # idempotent: already pointed
    return sections + [pointer]


def _append_archive(archive_file: Path, repo: str, source: str, cutoff: str, sections: list[Section]) -> None:
    """Append sections to the archive file (append-only; idempotent by heading)."""
    archive_file.parent.mkdir(parents=True, exist_ok=True)
    existing = archive_file.read_text(encoding="utf-8") if archive_file.is_file() else ""
    if not existing:
        existing = f"# Archived `{source}` — {repo} (cutoff {cutoff})\n\n"
    new_body = existing
    for sec in sections:
        # Idempotency: skip a section whose heading is already archived here.
        if sec.heading and f"## {sec.heading}\n" in new_body:
            continue
        if not new_body.endswith("\n"):
            new_body += "\n"
        new_body += sec.body
        if not new_body.endswith("\n"):
            new_body += "\n"
    archive_file.write_text(new_body, encoding="utf-8")


def _append_changelog(changelog: Path, lines: list[str]) -> None:
    existing = changelog.read_text(encoding="utf-8") if changelog.is_file() else "# Changelog\n"
    body = existing
    if not body.endswith("\n"):
        body += "\n"
    for line in lines:
        if line in existing:  # idempotent: don't duplicate an entry
            continue
        body += line + "\n"
    changelog.write_text(body, encoding="utf-8")


def format_plan(plan: PrunePlan, *, applied: bool) -> str:
    verb = "applied" if applied else "dry-run (no changes written)"
    lines = [f"prune {plan.repo} — {verb}; cutoff {plan.cutoff}; archive {plan.archive_dir}"]
    for m in plan.messages:
        lines.append(f"  {m}")
    if plan.moves:
        lines.append("  planned moves:")
        for m in plan.moves:
            lines.append(f"    - {m.source}: '{m.heading}' (item {m.matched_item})")
    if plan.changelog_lines:
        lines.append("  CHANGELOG additions:")
        for c in plan.changelog_lines:
            lines.append(f"    {c}")
    return "\n".join(lines)
