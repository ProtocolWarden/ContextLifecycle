# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""`cl reconcile` — Layer B of the ``.console/`` reconciliation chain.

Implements the on-demand reconciliation pass described in
``docs/architecture/console-reconciliation-spec.md`` (§3):

* :mod:`worksheet` — fail-soft loader for the untracked ``.console/reconcile.yaml``.
* :mod:`scrub`     — single source for the scrub-target vocabulary (§1).
* :mod:`privacy`   — private-archive path resolution (env/discovery, no literal).
* :mod:`check`     — the I1 gate (``cl reconcile check``, §3.2).
* :mod:`prune`     — move-and-trim of completed history (``cl reconcile prune``, §3.3).
* :mod:`index`     — generated status dashboard (``cl reconcile index``, §3.4).
"""

from __future__ import annotations
