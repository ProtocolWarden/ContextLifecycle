# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the console-encoding guard.

Regression cover for: `cl reconcile check` computed a GREEN verdict, then died
with UnicodeEncodeError printing the `->` glyph in its cross-repo routing line
on a cp1252 Windows console. The gate passed; the operator saw a traceback and
a non-zero exit.
"""

from __future__ import annotations

import io
import sys

import pytest
from typer.testing import CliRunner

from context_lifecycle.cli.console import ensure_printable_console
from context_lifecycle.cli.main import app

# One of each non-ASCII codepoint the CLI's report lines actually use.
REPORT_GLYPHS = "→ ⇒ — ─ § … ≥ ✓ ✗"


class _Cp1252Stream(io.TextIOBase):
    """A stream that encodes as cp1252 — i.e. a default Windows console.

    `reconfigure` mutates the declared encoding/errors the way a real
    TextIOWrapper does, so the guard can be observed taking effect.
    """

    def __init__(self) -> None:
        super().__init__()
        self._encoding = "cp1252"
        self._errors = "strict"
        self.written: list[str] = []

    @property
    def encoding(self) -> str:
        return self._encoding

    @property
    def errors(self) -> str:
        return self._errors

    def reconfigure(self, *, encoding: str | None = None, errors: str | None = None) -> None:
        if encoding is not None:
            self._encoding = encoding
        if errors is not None:
            self._errors = errors

    def write(self, s: str) -> int:
        # Mimic a real console: encoding happens at write time and raises
        # under 'strict', which is exactly how the original crash surfaced.
        s.encode(self._encoding, self._errors)
        self.written.append(s)
        return len(s)


def test_cp1252_stream_rejects_report_glyphs_before_the_guard():
    """Without the guard the failure is real — otherwise this suite proves nothing."""
    stream = _Cp1252Stream()
    with pytest.raises(UnicodeEncodeError):
        stream.write(REPORT_GLYPHS)


def test_guard_makes_report_glyphs_printable(monkeypatch):
    stream = _Cp1252Stream()
    monkeypatch.setattr(sys, "stdout", stream)
    ensure_printable_console()
    stream.write(REPORT_GLYPHS)
    assert stream.written == [REPORT_GLYPHS]


def test_guard_reconfigures_both_streams(monkeypatch):
    out, err = _Cp1252Stream(), _Cp1252Stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    ensure_printable_console()
    for stream in (out, err):
        assert stream.encoding == "utf-8"
        assert stream.errors == "replace"


def test_guard_falls_back_to_errors_only_when_encoding_is_refused(monkeypatch):
    """A stream with a detached buffer rejects an encoding change.

    Never raising on an unencodable character still matters, so the guard must
    retry with errors alone rather than give up.
    """

    class _RefusesEncoding(_Cp1252Stream):
        def reconfigure(self, *, encoding=None, errors=None):
            if encoding is not None:
                raise ValueError("cannot change encoding of a detached stream")
            super().reconfigure(errors=errors)

    stream = _RefusesEncoding()
    monkeypatch.setattr(sys, "stdout", stream)
    ensure_printable_console()
    assert stream.encoding == "cp1252"  # unchanged — the refusal stood
    assert stream.errors == "replace"   # but output can no longer raise
    stream.write(REPORT_GLYPHS)         # would raise under 'strict'


def test_guard_skips_streams_without_reconfigure(monkeypatch):
    """pytest capture objects and StringIO have no reconfigure; must not raise.

    Asserts the stream stays usable afterwards rather than just that the call
    returned — skipping must be a no-op, not a half-applied change.
    """
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.StringIO())
    ensure_printable_console()
    out.write(REPORT_GLYPHS)
    assert out.getvalue() == REPORT_GLYPHS


def test_guard_is_idempotent(monkeypatch):
    stream = _Cp1252Stream()
    monkeypatch.setattr(sys, "stdout", stream)
    ensure_printable_console()
    ensure_printable_console()
    assert stream.encoding == "utf-8"
    assert stream.errors == "replace"


def test_root_callback_runs_the_guard(monkeypatch):
    """The guard must be wired to the app, not merely importable."""
    calls: list[int] = []
    monkeypatch.setattr(
        "context_lifecycle.cli.main.ensure_printable_console",
        lambda: calls.append(1),
    )
    CliRunner().invoke(app, ["reconcile", "--help"])
    assert calls, "root callback did not invoke the console guard"


def test_cli_surface_is_unchanged_by_the_callback():
    """The callback takes no options, so `cl --help` still lists every command."""
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in ("hook", "session", "context", "reconcile", "ledger", "loop"):
        assert name in result.output
