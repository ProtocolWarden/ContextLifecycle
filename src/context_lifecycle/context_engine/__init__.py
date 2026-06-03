# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Context-injection engine (warm routing + cold store + consolidation).

This is the canonical home of the engine that began as a prototype in a
consumer repo's ``.context/.engine/`` (PlatformManifest, spec §1/§7). The modules
here are deliberately written to run in BOTH modes with no edits:

  * imported as a package (``context_lifecycle.context_engine.route``), and
  * scaffolded as standalone files into a repo's ``.context/.engine/`` by
    ``cl context init`` and called by the Claude hooks via
    ``python3 .context/.engine/route.py`` (no import path required at hook time).

The cross-module imports use a path-based fallback (``spec_from_file_location``)
precisely so the same file works in either location. ``cl context init`` copies
:data:`ENGINE_FILES` verbatim, keeping CL the single source of truth while the
hooks stay robust under whatever ``python3`` is on PATH.
"""

from __future__ import annotations

from pathlib import Path

#: Directory holding the engine source files (this package's directory).
ENGINE_DIR: Path = Path(__file__).resolve().parent

#: The engine modules scaffolded into a consumer repo's ``.context/.engine/``.
ENGINE_FILES: tuple[str, ...] = (
    "route.py",
    "cold.py",
    "consolidate.py",
    "distill.py",
    "campaign.py",
    "prune.py",
)


def engine_source_files() -> list[Path]:
    """Return the absolute paths of the engine files to scaffold (existing only)."""
    return [ENGINE_DIR / name for name in ENGINE_FILES if (ENGINE_DIR / name).is_file()]
