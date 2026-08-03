# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Console-encoding guard for the `cl` CLI.

Formatting must never be able to fail a command that already succeeded.
"""

from __future__ import annotations

import sys


def ensure_printable_console() -> None:
    """Stop console encoding from crashing a command that already did its work.

    Windows consoles default to cp1252, which cannot encode the arrows, box
    drawing, section signs and em-dashes this CLI's reports use. Printing them
    raises ``UnicodeEncodeError`` *after* the command has finished, discarding
    the result the operator asked for and exiting non-zero — a green check
    reported as a failure.

    Concretely: ``cl reconcile check`` on a worksheet containing any cross-repo
    item died on the ``->`` glyph in its routing line, having already computed a
    GREEN verdict. The gate passed; the operator saw a traceback.

    Replacing glyphs one at a time does not hold — the source carries nine
    distinct non-ASCII codepoints across ~940 occurrences, and any new report
    line can reintroduce the crash. Fixing the stream instead makes the whole
    class impossible: prefer UTF-8, and fall back to replacing unencodable
    characters so output degrades to ``?`` rather than raising.

    Idempotent, and safe to call on non-standard streams (pytest's capture
    objects have no ``reconfigure``); those are skipped.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pytest capture, io.StringIO, closed stream
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError, LookupError):  # pragma: no cover
            # Stream refuses a full reconfigure (detached buffer, exotic
            # terminal). Salvage what matters: never raise on an unencodable
            # character, even if the encoding itself cannot be changed.
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass
