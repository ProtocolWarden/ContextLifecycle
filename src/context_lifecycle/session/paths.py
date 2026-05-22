"""Per-session subdir layout helpers.

Layout (per ADR 0002 P0.5):
    <anchor>/.context/sessions/<session_id>/{active,checkpoints,handoffs}/
    <anchor>/.context/archived/<session_id>/...
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SessionPaths:
    anchor: Path
    session_id: str

    @property
    def context_root(self) -> Path:
        return self.anchor / ".context"

    @property
    def sessions_root(self) -> Path:
        return self.context_root / "sessions"

    @property
    def root(self) -> Path:
        return self.sessions_root / self.session_id

    @property
    def active(self) -> Path:
        return self.root / "active"

    @property
    def checkpoints(self) -> Path:
        return self.root / "checkpoints"

    @property
    def handoffs(self) -> Path:
        return self.root / "handoffs"

    @property
    def config_file(self) -> Path:
        return self.context_root / "config.yaml"

    @property
    def archived_root(self) -> Path:
        return self.context_root / "archived"

    @property
    def archived_target(self) -> Path:
        return self.archived_root / self.session_id

    def ensure(self) -> None:
        for d in (self.active, self.checkpoints, self.handoffs):
            d.mkdir(parents=True, exist_ok=True)


def session_root(anchor: Path, session_id: str) -> Path:
    return SessionPaths(anchor, session_id).root


def active_dir(anchor: Path, session_id: str) -> Path:
    return SessionPaths(anchor, session_id).active


def checkpoints_dir(anchor: Path, session_id: str) -> Path:
    return SessionPaths(anchor, session_id).checkpoints


def handoffs_dir(anchor: Path, session_id: str) -> Path:
    return SessionPaths(anchor, session_id).handoffs


def archived_root(anchor: Path) -> Path:
    return anchor / ".context" / "archived"
