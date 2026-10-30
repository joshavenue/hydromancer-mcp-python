"""Friendly time inputs. Users (and models) should not have to compute Unix milliseconds, so
every time argument accepts:
  - a relative span back from now: "30m", "6h", "24h", "7d", "2w" (optionally followed by "ago")
  - a date or date-time: "2026-10-01", "2026-10-01T12:00:00Z" (no offset means UTC)
  - a Unix timestamp in milliseconds or seconds
  - "now"
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone

UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
_REL = re.compile(r"^(\d+(?:\.\d+)?)\s*(m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|weeks?)(\s+ago)?$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EPOCH = re.compile(r"^\d{9,13}$")

TIME_DESCRIPTION = 'Relative ("30m", "6h", "24h", "7d"), a date ("2026-10-01"), a date-time ("2026-10-01T12:00:00Z"), "now", or Unix ms.'


def now_ms() -> int:
    return int(time.time() * 1000)


def _normalise_epoch(n: float) -> int:
    # 10-digit values are seconds; 13-digit values are milliseconds.
    return round(n * 1000) if n < 1e11 else round(n)


def parse_time(value: str | int | float | None, now: int | None = None) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f'Could not understand the time "{value}".')
    if isinstance(value, (int, float)):
        if not math.isfinite(value) or not 0 <= value < 1e14:
            raise ValueError(f'Could not understand the time "{value}". Use Unix seconds or milliseconds.')
        return _normalise_epoch(value)
    now = now_ms() if now is None else now
    raw = str(value).strip()
    s = raw.lower()
    if s == "now":
        return now
    rel = _REL.match(s)
    if rel:
        return round(now - float(rel.group(1)) * UNIT_MS[rel.group(2)[0]])
    if _EPOCH.match(s):
        return _normalise_epoch(int(s))
    iso = f"{raw}T00:00:00+00:00" if _DATE.match(raw) else raw
    if iso.endswith(("Z", "z")):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        raise ValueError(
            f'Could not understand the time "{value}". Use something like "24h", "7d", "2026-10-01" or "2026-10-01T12:00:00Z".'
        ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return round(dt.timestamp() * 1000)


def iso(ms: object) -> str | None:
    """Unix ms to an ISO-8601 UTC string like 2026-10-01T00:00:00.000Z."""
    try:
        n = int(float(ms))  # type: ignore[arg-type]
        dt = datetime.fromtimestamp(n / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{n % 1000:03d}Z"
