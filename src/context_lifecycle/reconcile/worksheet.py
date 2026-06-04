# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Worksheet loader for ``.console/reconcile.yaml`` (spec §3.1).

The worksheet is the untracked, transient scaffolding that drives a
reconciliation pass. The loader is **fail-soft**: a malformed item is skipped
with a warning and never raises, so a typo in one row can't block the whole gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from context_lifecycle.io.yaml_io import load_yaml_safe

WORKSHEET_RELPATH = Path(".console") / "reconcile.yaml"

_VALID_STATUS = {"done", "partial", "incomplete"}


@dataclass(frozen=True)
class ReconcileItem:
    """One worksheet row (§3.1)."""

    id: str
    title: str
    status: str
    owner: str
    doc: tuple[str, ...] = ()

    @property
    def is_done(self) -> bool:
        return self.status == "done"

    def is_cross_repo(self, repo: str) -> bool:
        return self.owner != repo


@dataclass
class Worksheet:
    """A parsed worksheet plus the warnings raised while loading it."""

    repo: str
    items: list[ReconcileItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    exists: bool = True

    def cross_repo(self) -> list[ReconcileItem]:
        return [it for it in self.items if it.is_cross_repo(self.repo)]

    def owned(self) -> list[ReconcileItem]:
        return [it for it in self.items if not it.is_cross_repo(self.repo)]


def _coerce_item(raw: Any, default_repo: str, warn: Callable[[str], None]) -> ReconcileItem | None:
    if not isinstance(raw, dict):
        warn(f"skipped worksheet item: not a mapping ({raw!r})")
        return None
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id.strip():
        warn(f"skipped worksheet item: missing/blank 'id' ({raw!r})")
        return None
    item_id = item_id.strip()
    status = raw.get("status")
    if not isinstance(status, str) or status.strip() not in _VALID_STATUS:
        warn(f"skipped item {item_id!r}: status must be one of {sorted(_VALID_STATUS)} (got {status!r})")
        return None
    status = status.strip()
    title = raw.get("title")
    title = title.strip() if isinstance(title, str) and title.strip() else item_id
    owner = raw.get("owner")
    owner = owner.strip() if isinstance(owner, str) and owner.strip() else default_repo
    raw_doc = raw.get("doc")
    docs: list[str] = []
    if raw_doc is None:
        pass
    elif isinstance(raw_doc, list):
        for d in raw_doc:
            if isinstance(d, str) and d.strip():
                docs.append(d.strip())
            else:
                warn(f"skipped doc entry in item {item_id!r}: not a path string ({d!r})")
    else:
        warn(f"ignored 'doc' in item {item_id!r}: must be a list (got {type(raw_doc).__name__})")
    return ReconcileItem(id=item_id, title=title, status=status, owner=owner, doc=tuple(docs))


def load_worksheet(repo_root: Path, *, fallback_repo: str | None = None) -> Worksheet:
    """Load ``<repo_root>/.console/reconcile.yaml`` fail-soft.

    A missing file yields an empty worksheet with ``exists=False``. A malformed
    document or item is skipped with a warning; the function never raises.
    ``fallback_repo`` (default: the directory name) names the repo when the
    document omits ``repo``.
    """
    repo_root = Path(repo_root)
    default_repo = fallback_repo or repo_root.name
    path = repo_root / WORKSHEET_RELPATH
    warnings: list[str] = []

    def warn(msg: str) -> None:
        warnings.append(msg)

    if not path.is_file():
        return Worksheet(repo=default_repo, items=[], warnings=warnings, exists=False)

    data = load_yaml_safe(path, default=None)
    if data is None:
        warn(f"worksheet {path} is empty or unparseable; treating as no items")
        return Worksheet(repo=default_repo, items=[], warnings=warnings, exists=True)
    if not isinstance(data, dict):
        warn(f"worksheet {path} root must be a mapping; treating as no items")
        return Worksheet(repo=default_repo, items=[], warnings=warnings, exists=True)

    repo_field = data.get("repo")
    repo = repo_field.strip() if isinstance(repo_field, str) and repo_field.strip() else default_repo

    raw_items = data.get("items")
    items: list[ReconcileItem] = []
    if raw_items is None:
        pass
    elif isinstance(raw_items, list):
        seen_ids: set[str] = set()
        for raw in raw_items:
            item = _coerce_item(raw, repo, warn)
            if item is None:
                continue
            if item.id in seen_ids:
                warn(f"skipped duplicate item id {item.id!r}")
                continue
            seen_ids.add(item.id)
            items.append(item)
    else:
        warn(f"worksheet {path} 'items' must be a list; treating as no items")

    return Worksheet(repo=repo, items=items, warnings=warnings, exists=True)
