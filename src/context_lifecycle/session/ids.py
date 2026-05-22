"""CL_SESSION_ID generation + env read.

Format per ADR 0002 P0.1: `s-<YYYY-MM-DD>-<4-char-rand>`.
"""

from __future__ import annotations

import os
import re
import secrets
from datetime import datetime, timezone

from context_lifecycle.errors import SessionNotStarted

SESSION_ID_RE = re.compile(r"^s-\d{4}-\d{2}-\d{2}-[a-z0-9]{4}$")
ENV_VAR = "CL_SESSION_ID"


def generate_session_id(now: datetime | None = None) -> str:
    """Generate a fresh session id of the locked format."""
    now = now or datetime.now(timezone.utc)
    date = now.strftime("%Y-%m-%d")
    # 4 hex chars from secrets — collision-safe enough for per-day session ids.
    rand = secrets.token_hex(2)
    return f"s-{date}-{rand}"


def is_valid_session_id(sid: str) -> bool:
    return bool(SESSION_ID_RE.match(sid))


def require_session_env() -> str:
    """Return CL_SESSION_ID or raise SessionNotStarted."""
    sid = os.environ.get(ENV_VAR, "").strip()
    if not sid:
        raise SessionNotStarted(
            "ContextLifecycle: no session id set (CL_SESSION_ID is unset). "
            "Run `eval $(cl session start <manifest>)` before invoking Claude Code in this repo."
        )
    return sid
