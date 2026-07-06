# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""PseudoOperator — the shared session-loop harness (Track B).

One config-parameterized engine replacing the two near-copy-paste
``tools/loop/controller.py`` files in OperationsCenter and VF.
The harness owns the mechanism (locking, signals, CL anchoring, backend
cooldown/fallback ladder, bounded session spawn, enforced caps, adaptive
delay); each repo supplies policy via ``PseudoOperatorConfig`` (prompt,
models, delay strategy, hook commands). See ``docs/design/pseudo_operator.md``.
"""

from .config import PseudoOperatorConfig, load_pseudo_operator_config
from .engine import PseudoOperatorEngine

__all__ = [
    "PseudoOperatorConfig",
    "PseudoOperatorEngine",
    "load_pseudo_operator_config",
]
