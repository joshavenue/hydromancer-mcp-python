"""Coin name resolution. Users type "btc", "$ETH", "BTC-PERP", "ETHUSDT", "xyz:tsla" or "kpepe";
Hyperliquid wants exact names such as "BTC", "kPEPE" or "xyz:TSLA". Names are resolved against the
live list of every market (allMids across all dexes), cached for 10 minutes.
"""

from __future__ import annotations

import re
import time

from .client import HydromancerClient

TTL_S = 600.0
_SUFFIX = re.compile(r"[-_/ ]?(perp|perps|usdc|usdt|usd)$", re.IGNORECASE)


def candidates(raw: str) -> list[str]:
    s = raw.strip()
    if s.startswith("$"):
        s = s[1:]
    out = [s]
    stripped = _SUFFIX.sub("", s)
    if stripped and stripped != s:
        out.append(stripped)
    return out


class CoinResolver:
    def __init__(self, client: HydromancerClient) -> None:
        self.client = client
        self._names: dict[str, str] | None = None
        self._at = 0.0

    async def _market_names(self) -> dict[str, str]:
        if self._names is not None and time.monotonic() - self._at < TTL_S:
            return self._names
        mids = await self.client.info({"type": "allMids", "dex": "ALL_DEXS"})
        names: dict[str, str] = {}
        for name in (mids or {}) if isinstance(mids, dict) else ():
            if name.startswith(("@", "#")):  # spot pair / outcome ids
                continue
            names[name.lower()] = name
        self._names, self._at = names, time.monotonic()
        return names

    async def resolve(self, raw: str) -> str:
        """Resolve one user-typed coin to the exact market name. Unknown names pass through trimmed.
        API errors (bad key, rate limit, network) propagate so the user sees the real cause."""
        if not raw:
            return raw
        names = await self._market_names()
        for c in candidates(raw):
            hit = names.get(c.lower())
            if hit:
                return hit
        return raw.strip()

    async def resolve_many(self, raws: list[str]) -> list[str]:
        return [await self.resolve(r) for r in raws]
