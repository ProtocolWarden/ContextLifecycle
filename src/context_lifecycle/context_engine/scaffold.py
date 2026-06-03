# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""``cl context init`` scaffolding — lay the context-injection engine + state
into a consumer repo, idempotently.

The engine (logic) is copied from the CL package into ``<repo>/.context/.engine/``
so the Claude hooks can call it by path under any ``python3`` (spec §1/§6). The
*state* surfaces — ``routes.yaml``, ``docs/inject/``, ``.context/knowledge/`` —
are scaffolded ONLY when absent, so re-running never clobbers a repo's own
routing table or authored leaf docs. The engine files ARE refreshed on every run
(CL is the single source of truth for engine logic).
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from context_lifecycle.context_engine import engine_source_files

# engine_compat range the scaffolded routes.yaml is stamped with — must satisfy
# route.SCHEMA_VERSION (0, 2). Kept as a literal (not imported) so init stays
# light and free of the engine's import side-effects.
ENGINE_COMPAT = ">=0.2 <0.3"

ROUTES_TEMPLATE = f"""\
# Context-injection routing table. glob -> leaf docs, ALL-MATCHES (every matching
# rule fires). Consumed by .context/.engine/route.py; active only when
# .context/config.yaml injection.enabled is true.
engine_compat: "{ENGINE_COMPAT}"   # must satisfy route.py SCHEMA_VERSION
budget:
  max_docs_per_edit: 3              # cap injected warm breadth per edit
  max_cold_surface_per_edit: 5      # cap one-line cold-topic surfacing per edit
routes: []
  # - match: "src/your_pkg/**"
  #   inject: ["docs/inject/your-doc.md"]
  #   priority: 20
"""

INJECT_README = """\
# Warm-tier leaf docs

Each file here is a convention doc injected (its `## Inject` section) before edits
to the paths routed to it in `.context/routes.yaml`. Keep `## Inject` tight and
prevention-oriented; put the long form under `## Reference`.
"""

KNOWLEDGE_README = """\
# Cold store (`.context/knowledge/`)

One `<slug>.md` per durable finding, in the §2.6 item format (YAML front-matter
+ `## Finding` / `## Detail`). Surfaced one-line by the router on matching edits;
promoted to warm by `cl consolidate` under the consequence-veto. The engine reads
these; do not hand-edit `tier`/`last_injected` casually.
"""


@dataclass
class InitReport:
    """What ``init_context`` did, for human-readable CLI output and tests."""

    created: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    refreshed: list[str] = field(default_factory=list)


def init_context(repo_root: Path) -> InitReport:
    """Scaffold the context-injection surfaces into ``repo_root``, idempotently.

    - ``.context/.engine/*.py``: copied from the CL package, ALWAYS refreshed.
    - ``.context/routes.yaml``, ``docs/inject/README.md``,
      ``.context/knowledge/README.md``: created ONLY when absent (never clobbered).

    Returns an :class:`InitReport`. Creates parent directories as needed.
    """
    report = InitReport()
    repo_root = Path(repo_root)

    # Engine (logic) — refresh from the CL package every run.
    engine_dir = repo_root / ".context" / ".engine"
    engine_dir.mkdir(parents=True, exist_ok=True)
    for src in engine_source_files():
        dst = engine_dir / src.name
        existed = dst.exists()
        shutil.copyfile(src, dst)
        rel = f".context/.engine/{src.name}"
        report.refreshed.append(rel) if existed else report.created.append(rel)

    # State surfaces — create only when absent.
    surfaces = [
        (repo_root / ".context" / "routes.yaml", ROUTES_TEMPLATE),
        (repo_root / "docs" / "inject" / "README.md", INJECT_README),
        (repo_root / ".context" / "knowledge" / "README.md", KNOWLEDGE_README),
    ]
    for path, content in surfaces:
        rel = str(path.relative_to(repo_root))
        if path.exists():
            report.skipped.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        report.created.append(rel)

    return report
