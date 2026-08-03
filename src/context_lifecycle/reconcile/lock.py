# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Per-repo mutation lock for `cl reconcile prune --apply`.

Two prunes applying concurrently against the same repo can interleave the
archive append and the source trim (same-section archived twice; the
idempotency-by-heading guard races its own read). An exclusive lock on
``.console/.reconcile.lock`` serializes appliers **on one host**; cross-host
runs are already serialized by git (append-only archive + idempotent re-run,
merged with ordinary rebase) — the lock closes the same-host window where no
merge point exists.

The lock file is untracked working state (like the worksheet) and is left in
place after release; holding pid is recorded for diagnostics only.

Platform backends
─────────────────
POSIX uses ``fcntl.flock``; Windows uses ``msvcrt.locking``. Both are
non-blocking, both release automatically if the holder dies, and both conflict
with a second handle opened by the *same* process — so a nested acquire raises
rather than silently succeeding.

They differ in one way that shapes the file layout. ``flock`` is advisory and
whole-file, so any reader can still read the pid. ``msvcrt.locking`` locks a
byte *range* and Windows enforces it: a reader touching a locked byte gets
``PermissionError``. The lock therefore claims a single sentinel byte at
``_LOCK_OFFSET``, clear of the pid field at offset 0, so a contending run can
still read and name the holder. Writing the pid as a fixed-width field avoids
truncating the file, which would otherwise cross the locked range.

Before 2026-08-03 this module raised on any non-POSIX host, so
``prune --apply`` was unrunnable on Windows — the reconciliation workflow
dead-ended there and logs got hand-pruned instead.
"""

from __future__ import annotations

import errno
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

try:
    import fcntl
except ImportError:  # Windows — msvcrt below is the backend there.
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt
except ImportError:  # POSIX — fcntl above is the backend there.
    msvcrt = None  # type: ignore[assignment]

LOCK_RELPATH = Path(".console") / ".reconcile.lock"

# Pid is written as a fixed-width field at offset 0, so a shorter pid cannot
# leave a longer predecessor's tail behind and no truncation is needed.
_PID_FIELD = 32
# The byte msvcrt actually locks. Must sit clear of the pid field: on Windows a
# locked range is mandatory, and a contending run reads the pid to name the
# holder. Unused by the POSIX backend, whose flock covers the whole file.
_LOCK_OFFSET = 1024

# Errnos meaning "someone else holds it" rather than "the call was wrong".
# POSIX flock reports EWOULDBLOCK/EAGAIN; msvcrt reports EACCES, and EDEADLOCK
# once its internal retries are exhausted.
_CONTENDED = frozenset(
    e for e in (
        getattr(errno, "EACCES", None),
        getattr(errno, "EAGAIN", None),
        getattr(errno, "EWOULDBLOCK", None),
        getattr(errno, "EDEADLOCK", None),
        getattr(errno, "EDEADLK", None),
    )
    if e is not None
)


class PruneLockHeld(RuntimeError):
    """Raised when another prune --apply holds the repo's reconcile lock."""


def _acquire(fd: int) -> None:
    """Take the exclusive lock without blocking. Raises OSError if contended."""
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)


def _release(fd: int) -> None:
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def _read_holder(fd: int) -> str:
    """Best-effort pid of the current holder, for the error message only."""
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        return os.read(fd, _PID_FIELD).decode("utf-8", "replace").strip()
    except OSError:
        return ""


@contextmanager
def reconcile_lock(repo_root: Path) -> Iterator[None]:
    """Hold the exclusive per-repo reconcile lock for the duration of the block.

    Non-blocking: raises :class:`PruneLockHeld` immediately if another process
    holds it — the caller should retry after the other prune finishes rather
    than queue behind it (the second run is a no-op anyway once the first
    lands).
    """
    if fcntl is None and msvcrt is None:  # pragma: no cover - neither backend
        raise RuntimeError(
            "reconcile prune --apply requires file locking (fcntl or msvcrt), "
            "and neither is available on this platform"
        )
    lock_path = Path(repo_root) / LOCK_RELPATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            _acquire(fd)
        except OSError as exc:
            if exc.errno not in _CONTENDED:
                raise  # a real error (bad fd, I/O) — never report it as contention
            holder = _read_holder(fd)
            detail = f" (held by pid {holder})" if holder else ""
            raise PruneLockHeld(
                f"another prune --apply is running against this repo{detail}; "
                f"lock: {lock_path}"
            ) from None
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, str(os.getpid()).ljust(_PID_FIELD).encode("utf-8"))
        try:
            yield
        finally:
            _release(fd)
    finally:
        os.close(fd)
