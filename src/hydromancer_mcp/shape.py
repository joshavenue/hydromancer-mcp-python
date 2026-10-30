"""Keep tool results small enough for a model to read. Lists are paged with limit/offset, and any
result is hard-capped with an explicit note so the model knows data was cut."""

from __future__ import annotations

import json
import math
import os
from typing import Any


def max_result_chars() -> int:
    try:
        value = int(os.environ.get("HYDROMANCER_MAX_RESULT_CHARS", "40000"))
    except ValueError:
        return 40_000
    return value if value > 0 else 40_000


def page(rows: list[Any], limit: int, offset: int = 0) -> tuple[list[Any], dict[str, Any]]:
    chunk = rows[offset : offset + limit]
    return chunk, {"total": len(rows), "offset": offset, "returned": len(chunk), "hasMore": offset + len(chunk) < len(rows)}


def to_text(payload: Any) -> str:
    text = json.dumps(payload, indent=1, ensure_ascii=False, default=str)
    cap = max_result_chars()
    if len(text) > cap:
        text = (
            text[:cap]
            + f"\n...[TRUNCATED: result was {len(text)} characters, cut to {cap}. Ask again with a smaller limit, "
            "a shorter time range, or one coin/address.]"
        )
    return text


def num(v: Any) -> float:
    if isinstance(v, bool):
        return float(v)
    try:
        n = float(v)
    except (TypeError, ValueError):
        return 0.0
    return n if math.isfinite(n) else 0.0


def rnd(n: float, dp: int = 2) -> float:
    return round(n, dp)
