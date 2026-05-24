from __future__ import annotations

from pathlib import Path

import pytest

from context_lifecycle.errors import (
    AmbiguousAnchor,
    AnchorInvalid,
    AnchorMissing,
    AnchorPrerequisitesMissing,
    ManifestNotFound,
)
from context_lifecycle.session import anchor as anchor_mod
from context_lifecycle.session.anchor import (
    ENV_VAR,
    require_anchor_env,
    resolve_anchor_arg,
    validate_anchor,
)


def test_require_anchor_env_missing(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(AnchorMissing):
        require_anchor_env()


def test_require_anchor_env_nonexistent(monkeypatch):
    monkeypatch.setenv(ENV_VAR, "/nope/does/not/exist")
    with pytest.raises(AnchorInvalid):
        require_anchor_env()


def test_require_anchor_env_present(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    assert require_anchor_env() == tmp_path.resolve()


class _FakeRecord:
    def __init__(self, name: str, root: Path):
        self.name = name
        self.root = root


class _FakeView:
    def __init__(self, records):
        self.manifests = {r.root: r for r in records}

    def get_by_name(self, name):
        for r in self.manifests.values():
            if r.name.lower() == name.lower():
                return r
        return None


class _FakeRepoGraph:
    """Default fake — replaceable in individual tests."""

    inferred = None
    records: list = []
    raise_ambiguous = False

    def find_anchor_for_path(self, _cwd):
        if self.raise_ambiguous:
            from repograph.errors import AmbiguousAnchorError
            raise AmbiguousAnchorError("two candidates")
        return self.inferred

    def authorization(self):
        return _FakeView(self.records)


def _patch_repograph(monkeypatch, fake_cls):
    monkeypatch.setattr(anchor_mod, "_load_repograph", lambda: fake_cls)


def test_resolve_anchor_arg_none_infers_via_repograph(monkeypatch, tmp_path):
    class Fake(_FakeRepoGraph):
        inferred = tmp_path

    _patch_repograph(monkeypatch, Fake)
    assert resolve_anchor_arg(None) == tmp_path.resolve()


def test_resolve_anchor_arg_none_no_match_errors(monkeypatch):
    class Fake(_FakeRepoGraph):
        inferred = None

    _patch_repograph(monkeypatch, Fake)
    with pytest.raises(ManifestNotFound, match="could not infer"):
        resolve_anchor_arg(None)


def test_resolve_anchor_arg_none_ambiguous_raises(monkeypatch):
    class Fake(_FakeRepoGraph):
        raise_ambiguous = True

    _patch_repograph(monkeypatch, Fake)
    with pytest.raises(AmbiguousAnchor):
        resolve_anchor_arg(None)


def test_resolve_anchor_arg_absolute(tmp_path):
    assert resolve_anchor_arg(str(tmp_path)) == tmp_path.resolve()


def test_resolve_anchor_arg_name_lookup(monkeypatch, tmp_path):
    pm_root = tmp_path / "PlatformManifest"
    pm_root.mkdir()

    class Fake(_FakeRepoGraph):
        records = [_FakeRecord("PlatformManifest", pm_root)]

    _patch_repograph(monkeypatch, Fake)
    assert resolve_anchor_arg("PlatformManifest") == pm_root


def test_resolve_anchor_arg_name_unknown_errors(monkeypatch):
    class Fake(_FakeRepoGraph):
        records = []

    _patch_repograph(monkeypatch, Fake)
    with pytest.raises(ManifestNotFound, match="not registered"):
        resolve_anchor_arg("NoSuchManifest")


def test_resolve_anchor_arg_nonexistent_path():
    with pytest.raises(ManifestNotFound):
        resolve_anchor_arg("/nope/missing/path")


def test_validate_anchor_skeleton_required(tmp_path):
    # no .context/ → fail
    with pytest.raises(AnchorPrerequisitesMissing):
        validate_anchor(tmp_path, require_context_skeleton=True)


def test_validate_anchor_skeleton_present(tmp_path):
    (tmp_path / ".context").mkdir()
    # Returns None and does not raise when the skeleton is present.
    assert validate_anchor(tmp_path, require_context_skeleton=True) is None
