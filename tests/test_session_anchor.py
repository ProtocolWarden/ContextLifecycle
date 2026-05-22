from __future__ import annotations

from pathlib import Path

import pytest

from context_lifecycle.errors import (
    AnchorInvalid,
    AnchorMissing,
    AnchorPrerequisitesMissing,
    ManifestNotFound,
)
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


def test_resolve_anchor_arg_none_raises():
    with pytest.raises(ManifestNotFound, match="Phase 2"):
        resolve_anchor_arg(None)


def test_resolve_anchor_arg_absolute(tmp_path):
    assert resolve_anchor_arg(str(tmp_path)) == tmp_path.resolve()


def test_resolve_anchor_arg_name_only_errors():
    with pytest.raises(ManifestNotFound, match="Phase 2"):
        resolve_anchor_arg("PlatformManifest")


def test_resolve_anchor_arg_nonexistent_path():
    with pytest.raises(ManifestNotFound):
        resolve_anchor_arg("/nope/missing/path")


def test_validate_anchor_skeleton_required(tmp_path):
    # no .context/ → fail
    with pytest.raises(AnchorPrerequisitesMissing):
        validate_anchor(tmp_path, require_context_skeleton=True)


def test_validate_anchor_skeleton_present(tmp_path):
    (tmp_path / ".context").mkdir()
    validate_anchor(tmp_path, require_context_skeleton=True)  # no raise
