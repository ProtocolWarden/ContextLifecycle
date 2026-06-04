# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Single source of truth for the scrub-target vocabulary (spec §1).

The "public-leak names" are the private platform names that must never sit in a
public repo's tracked files. Per spec §1 the set MUST be read from **one place**
— never hardcoded in multiple files, and (boundary rule I2) never hardcoded as a
string literal in this tracked source at all.

We therefore source the vocabulary entirely from the RepoGraph **boundary
disclosure artifact** (the same artifact Custodian's B-class detectors consume),
resolved via configuration / environment:

* ``$REPOGRAPH_BOUNDARY_ARTIFACT_FILE`` — path to the artifact JSON, or
* discovery under ``$PRIVATE_MANIFEST_DIR`` (``boundary/`` then ``dist/``).

The artifact's ``forbidden_names`` list supplies the literal names (both the
CamelCase and ``snake_case`` forms). Abbreviation patterns (e.g. a two-letter
word-boundary alias) are derived from the same artifact's listed names so there
is exactly one config location to edit (AC1).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

_ARTIFACT_FILE_ENV = "REPOGRAPH_BOUNDARY_ARTIFACT_FILE"
_PRIVATE_MANIFEST_ENV = "PRIVATE_MANIFEST_DIR"
# Relative artifact locations searched under the private-manifest root.
_ARTIFACT_DISCOVERY = (
    Path("boundary") / "boundary_disclosure_artifact.json",
    Path("dist") / "boundary_disclosure_artifact.json",
)


@dataclass(frozen=True)
class ScrubVocabulary:
    """Compiled scrub-target detection set, derived from one config source."""

    names: tuple[str, ...] = ()
    provenance: str = "none"
    _patterns: tuple[re.Pattern[str], ...] = field(default=(), repr=False)

    def matches(self, text: str | None) -> list[str]:
        """Return the distinct scrub-target names found in ``text`` (order-stable)."""
        if not text:
            return []
        hits: list[str] = []
        for name, pat in zip(self.names, self._patterns):
            if pat.search(text) and name not in hits:
                hits.append(name)
        return hits

    def is_empty(self) -> bool:
        return not self.names

    def redact(self, text: str, placeholder: str) -> str:
        """Replace every scrub-target occurrence in ``text`` with ``placeholder``."""
        out = text
        for pat in self._patterns:
            out = pat.sub(placeholder, out)
        return out


def _artifact_path() -> Path | None:
    """Resolve the boundary artifact path.

    Order: explicit ``$REPOGRAPH_BOUNDARY_ARTIFACT_FILE`` → ``$PRIVATE_MANIFEST_DIR``
    + discovery → RepoGraph-registry-discovered private root + discovery. The last
    fallback lets ``cl reconcile`` run with no env vars set (the common fan-out
    case) by reusing the same private-root resolution as the archive destination.
    """
    explicit = os.environ.get(_ARTIFACT_FILE_ENV, "").strip()
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None

    roots: list[Path] = []
    env_root = os.environ.get(_PRIVATE_MANIFEST_ENV, "").strip()
    if env_root:
        roots.append(Path(env_root).expanduser())
    else:
        # No env override → reuse the registry discovery used for the archive.
        from context_lifecycle.reconcile.privacy import _discover_via_repograph
        discovered = _discover_via_repograph()
        if discovered is not None:
            roots.append(discovered)

    for base in roots:
        for rel in _ARTIFACT_DISCOVERY:
            candidate = base / rel
            if candidate.is_file():
                return candidate
    return None


def _derive_abbreviations(names: Iterable[str]) -> list[str]:
    """Derive word-boundary abbreviations from CamelCase forbidden names.

    Spec §1 mandates a bare word-boundary alias (e.g. ``VF`` for
    ``VideoFoundry``) even though the artifact lists only the full
    CamelCase / ``snake_case`` forms. The alias is the upper-case
    initials of a multi-word CamelCase name; it is *derived* so the
    artifact stays the single source of truth (AC1) — the same
    derivation Custodian's ``load_scrub_targets`` applies, so both
    detection paths produce the identical vocabulary.
    """
    out: list[str] = []
    for name in names:
        caps = [c for c in name if c.isupper()]
        if len(caps) >= 2:
            out.append("".join(caps))
    return out


def _names_from_artifact(path: Path) -> list[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("forbidden_names")
    values = list(raw) if isinstance(raw, list) else []
    names = [str(item).strip() for item in values if str(item).strip()]
    return [*names, *_derive_abbreviations(names)]


def _compile(name: str) -> re.Pattern[str]:
    """Compile a detection pattern for one forbidden name.

    Short (<= 3 char) alias-style names match only on word boundaries so detector
    IDs that embed them (e.g. an ``XX2`` style id) are not false-positives. Longer
    names match as a plain case-insensitive substring (catches CamelCase and the
    ``snake_case`` form, which both appear verbatim in the artifact).
    """
    if len(name) <= 3:
        return re.compile(rf"\b{re.escape(name)}\b")
    return re.compile(re.escape(name), re.IGNORECASE)


def load_scrub_vocabulary(extra_names: Iterable[str] = ()) -> ScrubVocabulary:
    """Build the scrub vocabulary from the single configured source.

    ``extra_names`` lets callers (e.g. tests) inject the set without a real
    artifact on disk; in production the artifact is the sole source. The result
    is empty (and ``is_empty()``) when no source is configured — callers decide
    whether that is fatal.
    """
    names: list[str] = []
    provenance = "none"
    path = _artifact_path()
    if path is not None:
        names.extend(_names_from_artifact(path))
        if names:
            provenance = str(path)
    for extra in extra_names:
        extra = str(extra).strip()
        if extra:
            names.append(extra)
            if provenance == "none":
                provenance = "injected"
    # De-dupe, order-stable.
    seen: set[str] = set()
    unique: list[str] = []
    for n in names:
        if n not in seen:
            seen.add(n)
            unique.append(n)
    patterns = tuple(_compile(n) for n in unique)
    return ScrubVocabulary(names=tuple(unique), provenance=provenance, _patterns=patterns)
