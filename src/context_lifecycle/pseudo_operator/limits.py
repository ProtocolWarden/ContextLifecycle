# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 ProtocolWarden
"""Backend rate-limit detection: reset-time parsing + limit-kind classification.

Ported verbatim from the two live controllers (the five parser regexes were
byte-identical in both; the classifier regexes come from the OC controller).
This is the battle-tested piece — behavior changes here need fixtures from
real CLI limit messages.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

_TIMEZONE_RESET_RE = re.compile(
    r"resets\s+(\d{1,2}(?::\d{2})?(?:am|pm))\s+\(([^)]+)\)", re.IGNORECASE
)
# Per-model weekly limit form: "resets Jun 3, 9am (America/New_York)" — a month +
# day precede the clock time. The claude CLI emits this for Sonnet/Opus model
# limits (distinct from the plain "resets 9am (tz)" weekly form above).
_DATE_TIMEZONE_RESET_RE = re.compile(
    r"resets\s+([A-Za-z]{3,9})\s+(\d{1,2}),?\s+"
    r"(\d{1,2}(?::\d{2})?(?:am|pm))\s+\(([^)]+)\)",
    re.IGNORECASE,
)
_ISO_RESET_RE = re.compile(
    r"resets?(?:\s+at)?\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z)",
    re.IGNORECASE,
)
_RELATIVE_RESET_RE = re.compile(
    r"(?:try again|retry|resets?|reset|available again)[^\n]{0,80}?\bin\s+"
    r"(?:(?P<hours>\d+)\s*h(?:ours?)?)?\s*"
    r"(?:(?P<minutes>\d+)\s*m(?:in(?:ute)?s?)?)?\s*"
    r"(?:(?P<seconds>\d+)\s*s(?:ec(?:ond)?s?)?)?",
    re.IGNORECASE,
)
_LIMIT_SIGNAL_RE = re.compile(
    r"rate limit|usage limit|weekly limit|quota|too many requests|429"
    r"|hit your[^\n]{0,40}limit|sonnet limit|opus limit|claude limit",
    re.IGNORECASE,
)
# Signals a global Claude session/account limit — applies to all claude models,
# so opus won't help. Opus is only useful for sonnet-specific model rate limits.
GLOBAL_CLAUDE_LIMIT_RE = re.compile(
    r"5.hour|five.hour|session.limit|session.usage|account.limit|organization.limit",
    re.IGNORECASE,
)
# Split the global signal into the two operator-meaningful kinds for display.
_SESSION_LIMIT_RE = re.compile(r"5.hour|five.hour|session.limit|session.usage", re.IGNORECASE)
_ACCOUNT_LIMIT_RE = re.compile(r"account.limit|organi[sz]ation.limit", re.IGNORECASE)
_WEEKLY_LIMIT_RE = re.compile(r"weekly\s+limit|weekly\s+usage", re.IGNORECASE)
_EXPLICIT_MODEL_RE = re.compile(r"\b(sonnet|opus|haiku)\b", re.IGNORECASE)

RATE_LIMIT_BUFFER = 120  # seconds to wait after the stated reset time


def parse_rate_limit_reset(
    session_log: Path, backend: str = "claude"
) -> tuple[datetime | None, str | None]:
    """Return (reset_utc, log_text) when a rate limit is detected, else (None, None)."""
    try:
        text = session_log.read_text(encoding="utf-8", errors="replace")
        m = _DATE_TIMEZONE_RESET_RE.search(text)
        if m:
            month_str, day_str = m.group(1), m.group(2)
            time_str, tz_name = m.group(3).lower(), m.group(4)
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                logger.warning("Unknown timezone %r in %s limit message.", tz_name, backend)
                return None, text
            try:
                month = datetime.strptime(month_str[:3], "%b").month  # noqa: DTZ007
            except ValueError:
                logger.warning("Unparseable month %r in %s limit message.", month_str, backend)
                return None, text
            now_local = datetime.now(tz)
            time_format = "%I:%M%p" if ":" in time_str else "%I%p"
            parsed = datetime.strptime(time_str, time_format)  # noqa: DTZ007
            reset_local = now_local.replace(
                month=month,
                day=int(day_str),
                hour=parsed.hour,
                minute=parsed.minute,
                second=0,
                microsecond=0,
            )
            # No year in the message — if the date already passed this year, the
            # reset is next year.
            if reset_local <= now_local:
                reset_local = reset_local.replace(year=reset_local.year + 1)
            return reset_local.astimezone(timezone.utc), text

        m = _TIMEZONE_RESET_RE.search(text)
        if m:
            time_str, tz_name = m.group(1).lower(), m.group(2)
            try:
                tz = ZoneInfo(tz_name)
            except ZoneInfoNotFoundError:
                logger.warning("Unknown timezone %r in %s limit message.", tz_name, backend)
                return None, text
            now_local = datetime.now(tz)
            time_format = "%I:%M%p" if ":" in time_str else "%I%p"
            parsed = datetime.strptime(time_str, time_format)  # noqa: DTZ007
            reset_local = now_local.replace(
                hour=parsed.hour, minute=parsed.minute, second=0, microsecond=0
            )
            if reset_local <= now_local:
                reset_local += timedelta(days=1)
            return reset_local.astimezone(timezone.utc), text

        m = _ISO_RESET_RE.search(text)
        if m:
            return (
                datetime.fromisoformat(m.group(1).replace("Z", "+00:00")).astimezone(
                    timezone.utc
                ),
                text,
            )

        m = _RELATIVE_RESET_RE.search(text)
        if m:
            delta = timedelta(
                hours=int(m.group("hours") or 0),
                minutes=int(m.group("minutes") or 0),
                seconds=int(m.group("seconds") or 0),
            )
            if delta.total_seconds() > 0:
                return datetime.now(timezone.utc) + delta, text

        if _LIMIT_SIGNAL_RE.search(text):
            logger.info(
                "%s limit detected — no reset time parseable from %s.",
                backend.capitalize(),
                session_log.name,
            )
        return None, None
    except Exception as e:  # noqa: BLE001 — parsing must never crash the loop
        logger.warning("Failed to parse %s rate-limit reset time: %s", backend, e)
        return None, None


def classify_limit_kind(log_text: str | None, *, model: str | None) -> tuple[str, str | None]:
    """Return (limit_kind, model) for a backend limit.

    ``session_5h`` and ``global_weekly`` are account-wide (no model);
    ``model_weekly`` names the backend's model.
    """
    if log_text:
        if _SESSION_LIMIT_RE.search(log_text):
            return ("session_5h", None)
        if _ACCOUNT_LIMIT_RE.search(log_text):
            return ("global_weekly", None)
        if (
            _WEEKLY_LIMIT_RE.search(log_text)
            and not _EXPLICIT_MODEL_RE.search(log_text)
            and not _DATE_TIMEZONE_RESET_RE.search(log_text)
        ):
            return ("global_weekly", None)
    return ("model_weekly", model)


__all__ = [
    "GLOBAL_CLAUDE_LIMIT_RE",
    "RATE_LIMIT_BUFFER",
    "classify_limit_kind",
    "parse_rate_limit_reset",
]
