# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Tests for the archive pointer written into tracked files.

Regression cover for: `cl reconcile prune` embedded the archive's ABSOLUTE local
path in the source repo's `.console/log.md`. On one machine that put
`C:\\Users\\<name>\\...` into a PUBLIC repo — the operator's home directory and
real name — and the pointer was unfollowable on any other host or in CI.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from context_lifecycle.reconcile.privacy import (
    PRIVATE_ROOT_PLACEHOLDER,
    portable_archive_ref,
)
from context_lifecycle.reconcile.prune import (
    _POINTER_PREFIX,
    Section,
    _ensure_pointer,
    _pointer_section,
    apply_plan,
    build_plan,
)

# Shapes seen in the wild: a Windows run and an earlier Linux one.
_WINDOWS_ABS = PureWindowsPath(
    r"C:\Users\Some Operator\Documents\GitHub\PrivateManifest"
    r"\archive\console\Custodian\log-2026-08-03.md"
)
_POSIX_ABS = PurePosixPath(
    "/home/dev/Documents/GitHub/PrivateManifest"
    "/archive/console/PrivateManifest/log-2026-06-04.md"
)


def _looks_absolute(text: str) -> bool:
    return "C:\\" in text or "C:/" in text or "/home/" in text or "/Users/" in text


class TestPortableArchiveRef:
    def test_relative_to_a_known_private_root(self, tmp_path):
        root = tmp_path / "PrivateSide"
        target = root / "archive" / "console" / "Custodian" / "log-2026-08-03.md"
        ref = portable_archive_ref(target, private_root=root)
        assert ref == f"{PRIVATE_ROOT_PLACEHOLDER}/archive/console/Custodian/log-2026-08-03.md"

    def test_falls_back_to_the_archive_console_anchor(self):
        """No private_root supplied — slice at the known layout anchor."""
        ref = portable_archive_ref(Path(str(_POSIX_ABS)))
        assert ref == (
            f"{PRIVATE_ROOT_PLACEHOLDER}/archive/console/PrivateManifest/log-2026-06-04.md"
        )

    @pytest.mark.parametrize("raw", [str(_WINDOWS_ABS), str(_POSIX_ABS)])
    def test_never_emits_an_absolute_path(self, raw):
        """The whole point: no home directory, no operator name, ever."""
        ref = portable_archive_ref(Path(raw))
        assert not _looks_absolute(ref), ref
        assert "Some Operator" not in ref
        assert ref.startswith(PRIVATE_ROOT_PLACEHOLDER)

    def test_uses_forward_slashes_regardless_of_host(self, tmp_path):
        root = tmp_path / "P"
        ref = portable_archive_ref(
            root / "archive" / "console" / "R" / "log-2026-08-03.md", private_root=root
        )
        assert "\\" not in ref

    def test_archive_outside_the_given_root_still_relativises(self, tmp_path):
        """A mismatched private_root must not fall back to an absolute path."""
        ref = portable_archive_ref(Path(str(_POSIX_ABS)), private_root=tmp_path / "elsewhere")
        assert not _looks_absolute(ref), ref
        assert ref.startswith(PRIVATE_ROOT_PLACEHOLDER)

    def test_does_not_name_the_private_repo(self, tmp_path):
        """Boundary rule I2 — the private repo is resolved, never written down."""
        root = tmp_path / "SomePrivateRepoName"
        ref = portable_archive_ref(
            root / "archive" / "console" / "R" / "log.md", private_root=root
        )
        assert "SomePrivateRepoName" not in ref


class TestPointerSection:
    def test_pointer_body_is_portable(self, tmp_path):
        root = tmp_path / "P"
        sec = _pointer_section(
            root / "archive" / "console" / "Custodian" / "log-2026-08-03.md",
            private_root=root,
        )
        assert not _looks_absolute(sec.body), sec.body
        assert PRIVATE_ROOT_PLACEHOLDER in sec.body


class TestEnsurePointer:
    def _pointer(self, tmp_path) -> Section:
        root = tmp_path / "P"
        return _pointer_section(
            root / "archive" / "console" / "Custodian" / "log-2026-08-03.md",
            private_root=root,
        )

    def test_appends_when_absent(self, tmp_path):
        out = _ensure_pointer([], self._pointer(tmp_path))
        assert len(out) == 1

    def test_idempotent_when_already_portable(self, tmp_path):
        p = self._pointer(tmp_path)
        assert _ensure_pointer([p], p) == [p]

    def test_leaves_an_annotated_portable_pointer_untouched(self, tmp_path):
        """An operator's note beside the pointer must survive a re-run."""
        p = self._pointer(tmp_path)
        annotated = Section(
            heading=p.heading,
            body=p.body.rstrip() + "\n\n<!-- keep this note -->\n\n",
        )
        assert _ensure_pointer([annotated], p) == [annotated]

    @pytest.mark.parametrize("legacy_raw", [str(_WINDOWS_ABS), str(_POSIX_ABS)])
    def test_upgrades_a_legacy_absolute_pointer_in_place(self, tmp_path, legacy_raw):
        """The leak must not persist just because a pointer already exists."""
        legacy = Section(
            heading="Archived",
            body=f"## Archived\n\n_Archived completed history → `{legacy_raw}`_\n\n",
        )
        other = Section(heading="Other", body="## Other\n\nbody\n\n")
        out = _ensure_pointer([other, legacy], self._pointer(tmp_path))
        assert len(out) == 2
        assert out[0] is other, "unrelated sections must keep their position"
        assert not _looks_absolute(out[1].body), out[1].body
        assert PRIVATE_ROOT_PLACEHOLDER in out[1].body


class TestPruneEndToEnd:
    """Unit tests cover the pointer in isolation; this drives a real prune.

    The first version of this test used an ASCII `->` in the legacy pointer and
    passed the unit suite while producing TWO `## Archived` sections, because
    `_ensure_pointer` recognises a pointer by `_POINTER_PREFIX`, which carries a
    Unicode arrow. Building the fixture from the constant rather than retyping it
    is what makes this test honest.
    """

    def _repo(self, tmp_path: Path, legacy_body: str | None) -> Path:
        repo = tmp_path / "DemoRepo"
        (repo / ".console").mkdir(parents=True)
        (repo / "docs").mkdir()
        (repo / "docs" / "d.md").write_text("doc", encoding="utf-8")
        (repo / ".console" / "reconcile.yaml").write_text(
            "repo: DemoRepo\nitems:\n  - id: widget\n    title: widget\n"
            "    status: done\n    owner: DemoRepo\n    doc: [docs/d.md]\n",
            encoding="utf-8",
        )
        log = "# Log\n\n## 2026-05-30 — widget shipped\n\nBody.\n\n" + (legacy_body or "")
        (repo / ".console" / "log.md").write_text(log, encoding="utf-8")
        (repo / ".console" / "backlog.md").write_text(
            "# Backlog\n\n## Done\n\n- [x] widget\n\n", encoding="utf-8"
        )
        return repo

    def test_fresh_prune_writes_a_portable_pointer(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
        repo = self._repo(tmp_path, None)
        apply_plan(repo, build_plan(repo, private_root=tmp_path / "Priv"))
        log = (repo / ".console" / "log.md").read_text(encoding="utf-8")
        assert not _looks_absolute(log), log
        assert PRIVATE_ROOT_PLACEHOLDER in log

    def test_prune_heals_a_legacy_absolute_pointer(self, tmp_path, monkeypatch):
        monkeypatch.delenv("PRIVATE_MANIFEST_DIR", raising=False)
        legacy = (
            "## Archived\n\n"
            f"{_POINTER_PREFIX} `/home/dev/GitHub/Priv/archive/console/DemoRepo/log-old.md`_\n\n"
        )
        repo = self._repo(tmp_path, legacy)
        apply_plan(repo, build_plan(repo, private_root=tmp_path / "Priv"))
        log = (repo / ".console" / "log.md").read_text(encoding="utf-8")
        assert "/home/dev" not in log, "legacy absolute path survived the prune"
        assert log.count("## Archived") == 1, "healing must replace, not append"
        assert PRIVATE_ROOT_PLACEHOLDER in log
