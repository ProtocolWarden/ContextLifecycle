# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Minimal ``## `` (H2) section splitter for ``.console`` markdown files.

A "section" is an H2 heading line plus all lines up to (but not including) the
next H2 or EOF. Any preamble before the first H2 (e.g. the ``# Title`` line and
intro blurb) is captured as a headless ``preamble`` section so round-tripping
preserves it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_H2 = re.compile(r"^##\s+(?P<title>.+?)\s*$")


@dataclass
class Section:
    """One H2 section. ``heading`` is None for the file preamble."""

    heading: str | None
    body: str  # full text including the heading line (if any), trailing newline kept

    @property
    def title(self) -> str:
        return self.heading or ""


def split_sections(text: str) -> tuple[str, list[Section]]:
    """Split ``text`` into (preamble, [H2 sections]).

    Preamble is everything before the first H2 (may be empty). Each section's
    ``body`` is the verbatim slice so ``preamble + "".join(s.body)`` reconstructs
    the original.
    """
    lines = text.splitlines(keepends=True)
    preamble: list[str] = []
    sections: list[Section] = []
    current: Section | None = None
    for line in lines:
        m = _H2.match(line.rstrip("\n"))
        if m:
            if current is not None:
                sections.append(current)
            current = Section(heading=m.group("title").strip(), body=line)
        elif current is None:
            preamble.append(line)
        else:
            current.body += line
    if current is not None:
        sections.append(current)
    return "".join(preamble), sections


def join_sections(preamble: str, sections: list[Section]) -> str:
    return preamble + "".join(s.body for s in sections)
