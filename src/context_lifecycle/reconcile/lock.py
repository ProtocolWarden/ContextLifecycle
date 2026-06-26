# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Per-repo mutation lock for `cl reconcile prune --apply`.

Two prunes applying concurrently against the same repo can interleave the
archive append and the source trim (same-section archived twice; the
idempotency-by-heading guard races its own read). An exclusive ``flock`` on
``.console/.reconcile.lock`` serializes appliers **on one host**; cross-host
runs are already serialized by git (append-only archive + idempotent re-run,
merged with ordinary rebase) — the lock closes the same-host window where no
merge point exists.

The lock file is untracked working state (like the worksheet) and is left in
place after release; holding pid is recorded for diagnostics only.
"""

from __future__ import annotations

try:
    import fcntl
except ImportError:  # POSIX-only. On Windows only `reconcile prune --apply` needs
    fcntl = None  # type: ignore[assignment]  # it; importing this module must still work.
import os
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

LOCK_RELPATH = Path(".console") / ".reconcile.lock"


class PruneLockHeld(RuntimeError):
    """Raised when another prune --apply holds the repo's reconcile lock."""


@contextmanager
def reconcile_lock(repo_root: Path) -> Iterator[None]:
    """Hold the exclusive per-repo reconcile lock for the duration of the block.

    Non-blocking: raises :class:`PruneLockHeld` immediately if another process
    holds it — the caller should retry after the other prune finishes rather
    than queue behind it (the second run is a no-op anyway once the first
    lands).
    """
    if fcntl is None:
        raise RuntimeError(
            "reconcile prune --apply requires POSIX fcntl file locking, "
            "unavailable on this platform"
        )
    lock_path = Path(repo_root) / LOCK_RELPATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            holder = ""
            try:
                holder = os.read(fd, 64).decode("utf-8", "replace").strip()
            except OSError:
                pass
            detail = f" (held by pid {holder})" if holder else ""
            raise PruneLockHeld(
                f"another prune --apply is running against this repo{detail}; "
                f"lock: {lock_path}"
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
