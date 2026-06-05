# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl reconcile check` — the I1 gate (spec §3.2). Read-only.

Exit non-zero if any:
  (a) ``status: done`` item has an empty ``doc[]`` OR a ``doc`` path that does
      not exist  → **DOC GAP**;
  (b) any worksheet field contains a scrub-target name (§1)  → boundary (I2).
Otherwise green. Cross-repo items (``owner != repo``) are listed as routing
suggestions, never gated (§3.2 / AC7). This module never mutates anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from context_lifecycle.reconcile.scrub import ScrubVocabulary, load_scrub_vocabulary
from context_lifecycle.reconcile.worksheet import (
    ReconcileItem,
    Worksheet,
    load_worksheet,
)


@dataclass
class DocGap:
    item_id: str
    title: str
    reason: str  # "empty" | "missing:<path>"


@dataclass
class ScrubHit:
    item_id: str
    field: str
    names: tuple[str, ...]


@dataclass
class CheckResult:
    repo: str
    doc_gaps: list[DocGap] = field(default_factory=list)
    scrub_hits: list[ScrubHit] = field(default_factory=list)
    cross_repo: list[ReconcileItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def green(self) -> bool:
        return not self.doc_gaps and not self.scrub_hits


def _scan_fields(item: ReconcileItem, vocab: ScrubVocabulary) -> list[ScrubHit]:
    hits: list[ScrubHit] = []
    fields = {
        "id": item.id,
        "title": item.title,
        "status": item.status,
        "owner": item.owner,
    }
    for name, value in fields.items():
        found = vocab.matches(value)
        if found:
            hits.append(ScrubHit(item_id=item.id, field=name, names=tuple(found)))
    for idx, d in enumerate(item.doc):
        found = vocab.matches(d)
        if found:
            hits.append(ScrubHit(item_id=item.id, field=f"doc[{idx}]", names=tuple(found)))
    return hits


def run_check(
    repo_root: Path,
    *,
    vocab: ScrubVocabulary | None = None,
    worksheet: Worksheet | None = None,
) -> CheckResult:
    """Evaluate the I1 gate for the worksheet at ``repo_root`` (read-only)."""
    repo_root = Path(repo_root)
    ws = worksheet if worksheet is not None else load_worksheet(repo_root)
    vocabulary = vocab if vocab is not None else load_scrub_vocabulary()
    result = CheckResult(repo=ws.repo, warnings=list(ws.warnings))

    # A repo whose own name is a scrub target IS a private repo reconciling
    # itself — private names on private surfaces are not leaks (the boundary
    # protects PUBLIC surfaces), so the scrub gate is skipped. The DOC GAP
    # gate still applies in full.
    repo_is_private = bool(vocabulary.matches(ws.repo))
    if repo_is_private:
        result.warnings.append(
            f"{ws.repo} is a private repo (its name is a scrub target) — "
            "scrub-leak gate skipped (names are allowed on private surfaces)."
        )

    for item in ws.items:
        # (b) scrub-target leak in any field — public repos only (see above).
        if not repo_is_private:
            result.scrub_hits.extend(_scan_fields(item, vocabulary))
        # (a) DOC GAP — only for done items owned by this repo (the archive-eligible
        # set the gate protects; cross-repo items are routed, not gated).
        if item.is_done and not item.is_cross_repo(ws.repo):
            if not item.doc:
                result.doc_gaps.append(DocGap(item.id, item.title, "empty"))
            else:
                for d in item.doc:
                    if not (repo_root / d).exists():
                        result.doc_gaps.append(DocGap(item.id, item.title, f"missing:{d}"))
        if item.is_cross_repo(ws.repo):
            result.cross_repo.append(item)
    return result


def format_report(result: CheckResult) -> str:
    """Render a human-readable check report."""
    lines: list[str] = []
    for w in result.warnings:
        lines.append(f"warning: {w}")
    if result.doc_gaps:
        lines.append("DOC GAPs (done items lacking durable design docs):")
        for g in result.doc_gaps:
            detail = "empty doc[]" if g.reason == "empty" else g.reason.replace("missing:", "missing path: ")
            lines.append(f"  - {g.item_id}: {g.title} — {detail}")
    if result.scrub_hits:
        lines.append("SCRUB-TARGET leaks (boundary I2 — must remove before prune):")
        for h in result.scrub_hits:
            lines.append(f"  - {h.item_id}.{h.field}: {', '.join(h.names)}")
    if result.cross_repo:
        lines.append("Cross-repo items (route to owner; not gated here):")
        for it in result.cross_repo:
            lines.append(f"  - {it.id}: {it.title} → owner={it.owner} [{it.status}]")
    if result.green:
        lines.append(f"check: GREEN — {result.repo} is reconcilable (prune-ready).")
    else:
        n = len(result.doc_gaps) + len(result.scrub_hits)
        lines.append(f"check: BLOCKED — {result.repo} has {n} gate failure(s).")
    return "\n".join(lines)
