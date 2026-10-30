"""Hydromancer MCP tools. Each tool answers a question a trader would actually ask, accepts
friendly inputs (coin names in any case, "7d" style times), and returns a compact summary first
with the rows after it. Names, inputs and behaviour match the TypeScript Hydromancer MCP."""

import asyncio
import functools
from typing import Annotated, Any, Awaitable, Callable, Literal, Optional, Union

from mcp.types import ToolAnnotations
from pydantic import Field

from ._compat import ToolError
from .client import HydromancerClient, HydromancerError
from .coins import CoinResolver
from .shape import num, page, rnd, to_text
from .times import TIME_DESCRIPTION, iso, now_ms, parse_time

Obj = dict[str, Any]

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)

Address = Annotated[str, Field(pattern=r"^0x[0-9a-fA-F]{40}$", description="Wallet address, 0x followed by 40 hex characters.")]
Coin = Annotated[
    str,
    Field(min_length=1, description='Market name, any case: "BTC", "eth", "HYPE", "kPEPE", or a HIP-3 market such as "xyz:TSLA".'),
]
TimeArg = Optional[Union[str, int, float]]


def _since(default: str) -> Any:
    return Field(None, description=f'Start of window. {TIME_DESCRIPTION} Default "{default}".')


UNTIL = Field(None, description="End of window. Default now.")


def _limit(default: int, maximum: int) -> Any:
    return Field(None, ge=1, le=maximum, description=f"How many rows to return (default {default}, max {maximum}).")


OFFSET = Field(None, ge=0, description="Rows to skip, for paging.")


def window(since: Any, until: Any, default_start: str) -> tuple[int, Optional[int]]:
    start = parse_time(since if since not in (None, "") else default_start)
    assert start is not None
    end = parse_time(until)
    if end is not None and end <= start:
        raise ValueError("`until` must be later than `since`.")
    return start, end


def _window_out(start: int, end: Optional[int]) -> Obj:
    return {"from": iso(start), "to": iso(end if end is not None else now_ms())}


def _rows(value: Any) -> list[Obj]:
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _obj(value: Any) -> Obj:
    return value if isinstance(value, dict) else {}


# ---------------- summaries ----------------


def summarise_perp_state(state: Any) -> Obj:
    state = _obj(state)
    margin = _obj(state.get("marginSummary"))
    if not margin:
        return {"note": "No perp account found for this address."}
    positions = []
    for p in _rows(state.get("assetPositions")):
        pos = _obj(p.get("position"))
        size = num(pos.get("szi"))
        lev = _obj(pos.get("leverage"))
        cum = _obj(pos.get("cumFunding"))
        positions.append(
            {
                "coin": pos.get("coin"),
                "side": "long" if size > 0 else "short" if size < 0 else "flat",
                "size": abs(size),
                "valueUsd": rnd(num(pos.get("positionValue"))),
                "entryPrice": num(pos.get("entryPx")),
                "liquidationPrice": None if pos.get("liquidationPx") is None else num(pos.get("liquidationPx")),
                "leverage": f"{lev.get('value')}x {lev.get('type')}" if lev else None,
                "unrealizedPnlUsd": rnd(num(pos.get("unrealizedPnl"))),
                "returnOnEquityPct": rnd(num(pos.get("returnOnEquity")) * 100),
                "fundingSinceOpenUsd": rnd(-num(cum.get("sinceOpen"))) if cum else None,
            }
        )
    positions.sort(key=lambda x: x["valueUsd"], reverse=True)
    account_value = num(margin.get("accountValue"))
    total_ntl = num(margin.get("totalNtlPos"))
    return {
        "accountValueUsd": rnd(account_value),
        "totalPositionValueUsd": rnd(total_ntl),
        "marginUsedUsd": rnd(num(margin.get("totalMarginUsed"))),
        "withdrawableUsd": rnd(num(state.get("withdrawable"))),
        "effectiveLeverage": rnd(total_ntl / account_value) if account_value > 0 else None,
        "openPositions": len(positions),
        "positions": positions,
    }


# Unified-account and portfolio-margin wallets hold their money in spot; the perp accountValue
# then only reflects margin in open positions (or 0 with none).
SPOT_BALANCE_MODES = {"unifiedAccount", "portfolioMargin"}
STABLECOINS = {"USDC", "USDT0", "USDH", "USDE"}


def spot_stablecoins_usd(spot: Any) -> float:
    return sum(num(b.get("total")) for b in _rows(_obj(spot).get("balances")) if b.get("coin") in STABLECOINS)


def perp_account_value(chs: Any) -> float:
    chs = _obj(chs)
    if not chs:
        return 0.0
    if "marginSummary" in chs:
        return num(_obj(chs.get("marginSummary")).get("accountValue"))
    return sum(num(_obj(_obj(st).get("marginSummary")).get("accountValue")) for st in chs.values())


def balance_summary(mode: Any, chs: Any, spot: Any) -> Obj:
    """Best single balance figure for a wallet, honest about what it covers."""
    stables = spot_stablecoins_usd(spot)
    perp = perp_account_value(chs)
    spot_mode = mode in SPOT_BALANCE_MODES
    return {
        "accountMode": mode,
        "balanceUsd": rnd(stables if spot_mode else perp + stables),
        "balanceNote": (
            f"{mode} wallet: its balance lives in spot, so this is its spot stablecoin balance (USDC, USDT0, USDH, USDE). "
            "The perp accountValue below only shows margin in open positions. Other spot tokens are listed separately and not priced."
            if spot_mode
            else "Perp account value plus spot stablecoins (USDC, USDT0, USDH, USDE). Other spot tokens are listed separately and not priced."
        ),
    }


def summarise_fills(fills: list[Obj]) -> Obj:
    volume = pnl = fees = 0.0
    by_coin: dict[str, Obj] = {}
    for f in fills:
        v = num(f.get("px")) * num(f.get("sz"))
        volume += v
        pnl += num(f.get("closedPnl"))
        fees += num(f.get("fee"))
        c = by_coin.setdefault(str(f.get("coin")), {"fills": 0, "volumeUsd": 0.0, "realizedPnlUsd": 0.0})
        c["fills"] += 1
        c["volumeUsd"] += v
        c["realizedPnlUsd"] += num(f.get("closedPnl"))
    for c in by_coin.values():
        c["volumeUsd"] = rnd(c["volumeUsd"])
        c["realizedPnlUsd"] = rnd(c["realizedPnlUsd"])
    return {"fills": len(fills), "volumeUsd": rnd(volume), "realizedPnlUsd": rnd(pnl), "feesPaid": rnd(fees), "byCoin": by_coin}


def compact_fill(f: Obj) -> Obj:
    out: Obj = {
        "time": iso(f.get("time")),
        "coin": f.get("coin"),
        "action": f.get("dir"),
        "price": num(f.get("px")),
        "size": num(f.get("sz")),
        "valueUsd": rnd(num(f.get("px")) * num(f.get("sz"))),
        "realizedPnlUsd": rnd(num(f.get("closedPnl"))),
        "fee": num(f.get("fee")),
        "taker": f.get("crossed"),
    }
    if f.get("user"):
        out["user"] = f["user"]
    if f.get("builderFee"):
        out["builderFee"] = num(f["builderFee"])
    if f.get("liquidation"):
        out["liquidation"] = True
    return out


def _whole(n: float) -> Union[int, float]:
    return int(n) if n.is_integer() else n


def _time_key(r: Obj) -> float:
    return num(r.get("time"))


# ---------------- registration ----------------


def register_tools(server: Any, client: HydromancerClient) -> list[str]:
    names: list[str] = []
    resolver = CoinResolver(client)

    def tool(name: str, title: str, description: str) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> str:
                try:
                    return to_text(await fn(*args, **kwargs))
                except (HydromancerError, ValueError) as err:
                    raise ToolError(f"Error: {client.mask(str(err))}") from None
                except Exception as err:  # never leak a traceback (or the key) to the model
                    raise ToolError(f"Error: unexpected failure in {name} ({type(err).__name__}).") from None

            server.add_tool(wrapper, name=name, title=title, description=description, annotations=READ_ONLY, structured_output=False)
            names.append(name)
            return fn

        return deco

    # ---------- Wallets ----------
    @tool(
        "hydromancer_wallet_overview",
        "Wallet overview: balance, open positions, PnL record",
        "One-call profile of any Hyperliquid wallet: account value, every open perp position (side, size, entry, liquidation "
        "price, leverage, unrealized PnL), spot balances and its realized trading record (PnL, win rate, trade count, volume, "
        'human score). Use this first for questions like "what is this wallet holding?" or "is this trader any good?". '
        "Set includeHip3=true to include HIP-3 markets such as xyz:TSLA.",
    )
    async def wallet_overview(
        address: Address,
        includeHip3: Annotated[
            Optional[bool], Field(description="Also include positions on HIP-3 markets (stocks, indices, commodities). Default false.")
        ] = None,
    ) -> Obj:
        async def pnl_summary() -> Obj:
            try:
                return _obj(await client.info({"type": "userPnlSummary", "user": address}))
            except HydromancerError as e:
                return {"error": str(e)}

        portfolio_raw, pnl = await asyncio.gather(
            client.info({"type": "portfolioState", "user": address, "dex": "ALL_DEXES" if includeHip3 else None}),
            pnl_summary(),
        )
        portfolio = _obj(portfolio_raw)
        chs = portfolio.get("clearinghouseState")
        if includeHip3 and isinstance(chs, dict) and "marginSummary" not in chs:
            perp: Obj = {}
            for dex, st in chs.items():
                s = summarise_perp_state(st)
                if s.get("openPositions", 0) > 0 or dex == "native":
                    perp[dex] = s
        else:
            perp = summarise_perp_state(chs)
        spot_state = portfolio.get("spotClearinghouseState")
        spot = [
            {"token": b.get("coin"), "amount": num(b.get("total")), "onHold": num(b.get("hold")), "costBasisUsd": rnd(num(b.get("entryNtl")))}
            for b in _rows(_obj(spot_state).get("balances"))
            if num(b.get("total")) != 0
        ]
        if pnl and "error" not in pnl:
            record: Any = {
                "realizedPnlUsd": rnd(num(pnl.get("totalPnl"))),
                "winRatePct": None if pnl.get("winRate") is None else rnd(num(pnl.get("winRate")) * 100),
                "completedTrades": pnl.get("totalTrades"),
                "volumeTradedUsd": rnd(num(pnl.get("volumeTraded"))),
                "feesPaidUsd": rnd(num(pnl.get("totalFees"))),
                "fundingUsd": rnd(num(pnl.get("totalFunding"))),
                "daysActive": pnl.get("daysActive"),
                "accountAgeDays": pnl.get("accountAgeDays"),
                "humanScore": pnl.get("humanScore"),
                "humanScoreMeaning": "0-100; low scores suggest a bot or market maker.",
                "marketsTraded": pnl.get("tradedPairs"),
            }
        else:
            record = pnl or None
        return {
            "address": address,
            **balance_summary(portfolio.get("userAbstraction"), chs, spot_state),
            "perp": perp,
            "spotBalances": spot,
            "tradingRecord": record,
        }

    @tool(
        "hydromancer_wallet_trades",
        "Wallet trade history (fills)",
        "Every fill (buy/sell execution) a wallet made in a time window, newest first, with totals: volume, realized PnL, fees "
        f"and a per-coin breakdown. since/until accept {TIME_DESCRIPTION} Default window: last 24h. Large orders split into "
        "many fills are merged by default.",
    )
    async def wallet_trades(
        address: Address,
        since: TimeArg = _since("24h"),
        until: TimeArg = UNTIL,
        coin: Optional[Coin] = None,
        mergeSplitOrders: Annotated[Optional[bool], Field(description="Merge pieces of one order into a single fill (default true).")] = None,
        limit: Optional[int] = _limit(100, 2000),
        offset: Optional[int] = OFFSET,
    ) -> Obj:
        start, end = window(since, until, "24h")
        fills = _rows(
            await client.info(
                {
                    "type": "userFillsByTime",
                    "user": address,
                    "startTime": start,
                    "endTime": end,
                    "aggregateByTime": True if mergeSplitOrders is None else mergeSplitOrders,
                }
            )
        )
        rows = fills
        if coin:
            c = await resolver.resolve(coin)
            rows = [f for f in rows if f.get("coin") == c]
        rows = sorted(rows, key=_time_key, reverse=True)
        chunk, paging = page([compact_fill(f) for f in rows], limit or 100, offset or 0)
        out: Obj = {"window": _window_out(start, end), "totals": summarise_fills(rows)}
        if len(fills) >= 2000:
            out["note"] = "The API returned its 2,000-fill maximum; narrow the time window to see everything."
        out.update(paging=paging, fills=chunk)
        return out

    @tool(
        "hydromancer_wallet_closed_trades",
        "Wallet closed trades (open-to-close round trips)",
        "Completed trades for a wallet: each row is a full position from open to close with entry, exit, size, gross and net "
        'PnL, fees, funding and how long it was held. Best tool for judging trading skill ("what is this wallet\'s win rate '
        f'and best coin?"). Default window: last 30 days. since/until accept {TIME_DESCRIPTION}',
    )
    async def wallet_closed_trades(
        address: Address,
        since: TimeArg = _since("30d"),
        until: TimeArg = UNTIL,
        coin: Optional[Coin] = None,
        limit: Optional[int] = _limit(100, 500),
    ) -> Obj:
        start, end = window(since, until, "30d")
        rows = _rows(
            await client.info(
                {"type": "userCompletedTradesByTime", "user": address, "startTime": start, "endTime": end, "limit": limit or 100}
            )
        )
        if coin:
            c = await resolver.resolve(coin)
            rows = [t for t in rows if t.get("coin") == c]
        wins = sum(1 for t in rows if num(t.get("net_pnl")) > 0)
        by_coin: dict[str, Obj] = {}
        for t in rows:
            b = by_coin.setdefault(str(t.get("coin")), {"trades": 0, "netPnlUsd": 0.0})
            b["trades"] += 1
            b["netPnlUsd"] = rnd(b["netPnlUsd"] + num(t.get("net_pnl")))
        holds = sorted(num(t.get("duration_ms")) for t in rows)
        return {
            "window": _window_out(start, end),
            "summary": {
                "trades": len(rows),
                "winRatePct": rnd(wins / len(rows) * 100) if rows else None,
                "netPnlUsd": rnd(sum(num(t.get("net_pnl")) for t in rows)),
                "medianHoldMinutes": rnd(holds[len(holds) // 2] / 60_000, 1) if holds else None,
                "byCoin": by_coin,
            },
            "trades": [
                {
                    "coin": t.get("coin"),
                    "side": t.get("position_type"),
                    "opened": iso(t.get("open_time")),
                    "closed": iso(t.get("close_time")),
                    "holdMinutes": rnd(num(t.get("duration_ms")) / 60_000, 1),
                    "entryPrice": num(t.get("entry_px")),
                    "exitPrice": num(t.get("exit_px")),
                    "size": num(t.get("position_closed_size")),
                    "grossPnlUsd": rnd(num(t.get("gross_pnl"))),
                    "netPnlUsd": rnd(num(t.get("net_pnl"))),
                    "feesUsd": rnd(num(t.get("fees"))),
                    "fundingUsd": rnd(num(t.get("funding_pnl"))),
                }
                for t in rows
            ],
        }

    # ---------- Traders ----------
    @tool(
        "hydromancer_top_traders",
        "Top traders leaderboard",
        "Rank Hyperliquid traders by realized profit, win rate or volume over 1 day to all time, with filters that remove bots "
        'and one-off wallets. Use for "who are the best traders this week?", "find consistent winners on stocks (dex xyz)" or '
        '"top traders using builder X". Pair with hydromancer_wallet_overview to see what they hold now.',
    )
    async def top_traders(
        period: Annotated[Optional[Literal["1d", "7d", "30d", "90d", "all"]], Field(description='Time period (default "30d").')] = None,
        rankBy: Annotated[Optional[Literal["profit", "winRate", "volume"]], Field(description='Ranking (default "profit").')] = None,
        limit: Optional[int] = _limit(25, 1000),
        offset: Optional[int] = OFFSET,
        minTrades: Annotated[Optional[int], Field(ge=0, description="Minimum completed trades (default 5).")] = None,
        minDaysActive: Annotated[Optional[int], Field(ge=0, description="Minimum distinct active days (default 1).")] = None,
        excludeBots: Annotated[
            Optional[bool], Field(description="Drop wallets that look automated (human score below 60). Default true.")
        ] = None,
        market: Annotated[
            Optional[str], Field(description='Only "main" (native Hyperliquid) or a HIP-3 dex such as "xyz". Omit for all.')
        ] = None,
        builder: Annotated[
            Optional[str],
            Field(pattern=r"^0x[0-9a-fA-F]{40}$", description="Only traders who have traded through this builder's app (0x address)."),
        ] = None,
    ) -> Obj:
        sort_by = {"profit": "totalPnl", "winRate": "winRate", "volume": "volumeTraded"}[rankBy or "profit"]
        res = _obj(
            await client.info(
                {
                    "type": "userPnlLeaderboard",
                    "window": period or "30d",
                    "sortBy": sort_by,
                    "limit": limit or 25,
                    "offset": offset,
                    "minTrades": minTrades,
                    "minDaysActive": minDaysActive,
                    "minHumanScore": None if excludeBots is False else 60,
                    "dex": "main_dex" if market == "main" else market,
                    "builder": builder,
                }
            )
        )
        return {
            "period": period or "30d",
            "rankedBy": rankBy or "profit",
            "matchingTraders": res.get("total"),
            "traders": [
                {
                    "rank": (offset or 0) + i + 1,
                    "address": u.get("user"),
                    "realizedPnlUsd": rnd(num(u.get("totalPnl"))),
                    "winRatePct": None if u.get("winRate") is None else rnd(num(u.get("winRate")) * 100),
                    "trades": u.get("totalTrades"),
                    "volumeUsd": rnd(num(u.get("volumeTraded"))),
                    "daysActive": u.get("daysActive"),
                    "accountAgeDays": u.get("accountAgeDays"),
                    "humanScore": u.get("humanScore"),
                    "markets": (u.get("tradedPairs") or [])[:10],
                }
                for i, u in enumerate(_rows(res.get("users")))
            ],
        }

    # ---------- Markets ----------
    @tool(
        "hydromancer_market_snapshot",
        "Market snapshot: price, 24h volume, open interest",
        "Current mark/oracle/mid price, 24h volume, open interest (in coins and USD) and max leverage for one or more markets. "
        'Leave coins empty for the top markets by 24h volume. Works for HIP-3 markets (e.g. "xyz:TSLA").',
    )
    async def market_snapshot(
        coins: Annotated[
            Optional[list[str]],
            Field(max_length=20, description='Markets to show, e.g. ["BTC","ETH","xyz:TSLA"]. Omit for the busiest markets.'),
        ] = None,
        top: Annotated[
            Optional[int], Field(ge=1, le=100, description="When coins is empty, how many top markets by 24h volume (default 15).")
        ] = None,
    ) -> Obj:
        if coins:
            names_ = await resolver.resolve_many(coins)
            ctx = _obj(await client.info({"type": "assetContext", "coins": names_}))
            markets = []
            for c in names_:
                x = ctx.get(c)
                if not isinstance(x, dict):
                    markets.append({"coin": c, "note": "Unknown market. Check the spelling, or use the HIP-3 prefix such as xyz:TSLA."})
                    continue
                markets.append(
                    {
                        "coin": c,
                        "markPrice": num(x.get("markPx")),
                        "oraclePrice": num(x.get("oraclePx")),
                        "midPrice": None if x.get("midPx") is None else num(x.get("midPx")),
                        "volume24hUsd": rnd(num(x.get("dayNtlVlm"))),
                        "openInterestCoins": num(x.get("openInterest")),
                        "openInterestUsd": rnd(num(x.get("openInterest")) * num(x.get("markPx"))),
                    }
                )
            return {"markets": markets}
        res = await client.info({"type": "metaAndAssetCtxs"})
        meta, ctxs = (res[0], res[1]) if isinstance(res, list) and len(res) >= 2 else ({}, [])
        universe = _rows(_obj(meta).get("universe"))
        ctxs = ctxs if isinstance(ctxs, list) else []
        rows = []
        for i, u in enumerate(universe):
            if u.get("isDelisted"):
                continue
            c = _obj(ctxs[i]) if i < len(ctxs) else {}
            rows.append(
                {
                    "coin": u.get("name"),
                    "markPrice": num(c.get("markPx")),
                    "volume24hUsd": rnd(num(c.get("dayNtlVlm"))),
                    "openInterestUsd": rnd(num(c.get("openInterest")) * num(c.get("markPx"))),
                    "maxLeverage": u.get("maxLeverage"),
                }
            )
        rows.sort(key=lambda r: r["volume24hUsd"], reverse=True)
        return {
            "note": "Native Hyperliquid perps, sorted by 24h volume. HIP-3 markets: pass coins such as xyz:TSLA. For funding "
            "rates use hydromancer_funding_history; for 24h change use hydromancer_price_history.",
            "markets": rows[: top or 15],
        }

    @tool(
        "hydromancer_price_history",
        "Price candles (OHLCV)",
        "Price history as candles (open, high, low, close, volume) for any market. Intervals from 1 second to 1 month. "
        f"Default: 1h candles for the last 24h. since/until accept {TIME_DESCRIPTION}",
    )
    async def price_history(
        coin: Coin,
        interval: Annotated[
            Optional[Literal["1s", "15s", "30s", "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "8h", "12h", "1d", "3d", "1w", "1M"]],
            Field(description='Candle size (default "1h"). "1s" only covers about the last 30 minutes.'),
        ] = None,
        since: TimeArg = _since("24h"),
        until: TimeArg = UNTIL,
    ) -> Obj:
        c = await resolver.resolve(coin)
        start, end = window(since, until, "24h")
        iv = interval or "1h"
        candles = _rows(
            await client.info(
                {"type": "candleSnapshot", "req": {"coin": c, "interval": iv, "startTime": start, "endTime": end if end is not None else now_ms()}}
            )
        )
        summary = None
        if candles:
            first_open = num(candles[0].get("o"))
            last_close = num(candles[-1].get("c"))
            summary = {
                "open": first_open,
                "close": last_close,
                "high": max(num(k.get("h")) for k in candles),
                "low": min(num(k.get("l")) for k in candles),
                "changePct": rnd((last_close - first_open) / first_open * 100) if first_open else None,
                "volumeUsd": rnd(
                    sum(num(k["q"]) if k.get("q") is not None else num(k.get("v")) * num(k.get("c")) for k in candles)
                ),
            }
        return {
            "coin": c,
            "interval": iv,
            "candles": len(candles),
            "summary": summary,
            "rows": [
                {
                    "time": iso(k.get("t")),
                    "open": num(k.get("o")),
                    "high": num(k.get("h")),
                    "low": num(k.get("l")),
                    "close": num(k.get("c")),
                    "volume": num(k.get("v")),
                    "trades": k.get("n"),
                }
                for k in candles
            ],
        }

    @tool(
        "hydromancer_funding_history",
        "Funding rate history for a market",
        "Hourly funding rates for a market over time, with the average and annualized rate. Positive funding means longs pay "
        "shorts. Default window: last 7 days.",
    )
    async def funding_history(coin: Coin, since: TimeArg = _since("7d"), until: TimeArg = UNTIL) -> Obj:
        c = await resolver.resolve(coin)
        start, end = window(since, until, "7d")
        rows = _rows(await client.info({"type": "fundingHistory", "coin": c, "startTime": start, "endTime": end}))
        rates = [num(r.get("fundingRate")) for r in rows]
        avg = sum(rates) / len(rates) if rates else 0.0
        out: Obj = {
            "coin": c,
            "periods": len(rows),
            "averageHourlyPct": rnd(avg * 100, 5),
            "annualizedPct": rnd(avg * 24 * 365 * 100, 2),
            "latest": [{"time": iso(r.get("time")), "ratePct": rnd(num(r.get("fundingRate")) * 100, 5)} for r in reversed(rows[-24:])],
        }
        if len(rows) >= 500:
            out["note"] = "Capped at 500 periods; use a shorter window for full detail."
        return out

    @tool(
        "hydromancer_recent_liquidations",
        "Recent liquidations",
        "Positions that were force-closed (liquidated), newest first, for one market or the whole exchange, with totals by "
        'side. "Long liquidated" means a long position was wiped out. Default window: last 24h.',
    )
    async def recent_liquidations(
        coin: Annotated[Optional[str], Field(min_length=1, description="Market to check; omit for every market.")] = None,
        since: TimeArg = _since("24h"),
        until: TimeArg = UNTIL,
        limit: Optional[int] = _limit(50, 1000),
    ) -> Obj:
        c = await resolver.resolve(coin) if coin else None
        start, end = window(since, until, "24h")
        rows = _rows(
            await client.info({"type": "liquidationHistoryByTime", "coin": c, "startTime": start, "endTime": end, "limit": 1000})
        )
        rows.sort(key=_time_key, reverse=True)
        long_usd = short_usd = 0.0
        by_coin: dict[str, float] = {}
        for r in rows:
            v = num(r.get("px")) * num(r.get("sz"))
            if r.get("side") == "A":
                long_usd += v
            else:
                short_usd += v
            k = str(r.get("coin"))
            by_coin[k] = rnd(by_coin.get(k, 0.0) + v)
        top_coins = [{"coin": k, "liquidatedUsd": v} for k, v in sorted(by_coin.items(), key=lambda kv: kv[1], reverse=True)[:10]]
        out: Obj = {
            "window": _window_out(start, end),
            "totals": {
                "liquidations": len(rows),
                "longsLiquidatedUsd": rnd(long_usd),
                "shortsLiquidatedUsd": rnd(short_usd),
                "topCoins": top_coins,
            },
        }
        if len(rows) >= 1000:
            out["note"] = "Capped at the 1,000 most recent liquidations; narrow the window or pick a coin for full totals."
        out["liquidations"] = [
            {
                "time": iso(r.get("time")),
                "coin": r.get("coin"),
                "liquidated": "long" if r.get("side") == "A" else "short",
                "address": r.get("user") or _obj(r.get("liquidation")).get("liquidatedUser"),
                "price": num(r.get("px")),
                "size": num(r.get("sz")),
                "valueUsd": rnd(num(r.get("px")) * num(r.get("sz"))),
                "realizedPnlUsd": rnd(num(r.get("closedPnl"))),
            }
            for r in rows[: limit or 50]
        ]
        return out

    @tool(
        "hydromancer_market_depth",
        "Order book depth and slippage",
        "How much can be bought or sold near the current price. Returns USD depth on each side within 0.1% to 2% of the price "
        "for a market, plus the expected slippage for a market order of a given size over time.",
    )
    async def market_depth(
        coin: Coin,
        orderSizeUsd: Annotated[
            Optional[float], Field(gt=0, description="Order size for the slippage estimate, in USD (default 100,000).")
        ] = None,
        since: Annotated[TimeArg, Field(description='Slippage history window. Default "24h".')] = None,
    ) -> Obj:
        c = await resolver.resolve(coin)
        start, _ = window(since, None, "24h")
        size = orderSizeUsd or 100_000

        async def slippage() -> list[Obj]:
            try:
                return _rows(
                    await client.info({"type": "slippageHistory", "coin": c, "amount": size, "startTime": start, "limit": 2000})
                )
            except HydromancerError:
                return []

        depth_raw, slip = await asyncio.gather(client.info({"type": "currentDepth"}), slippage())
        depth = _obj(depth_raw)
        bare = c.split(":", 1)[1] if ":" in c else c
        row = next((d for d in _rows(depth.get("data")) if d.get("coin") in (c, bare)), None)

        def avg(key: str) -> Optional[float]:
            return rnd(sum(num(r.get(key)) for r in slip) / len(slip), 2) if slip else None

        return {
            "coin": c,
            "depthAt": iso(depth["timestampMs"]) if depth.get("timestampMs") else None,
            "depth": [
                {"withinPct": num(lv.get("bps")) / 100, "bidDepthUsd": rnd(num(lv.get("bidDepthUsd"))), "askDepthUsd": rnd(num(lv.get("askDepthUsd")))}
                for lv in _rows(row.get("levels"))
            ]
            if row
            else "No depth data for this market.",
            "slippage": {
                "orderSizeUsd": size,
                "samples": len(slip),
                "averageBuyBps": avg("buySlippageBps"),
                "averageSellBps": avg("sellSlippageBps"),
                "latest": {"time": iso(slip[-1].get("timestamp")), "buyBps": slip[-1].get("buySlippageBps"), "sellBps": slip[-1].get("sellSlippageBps")}
                if slip
                else None,
                "bpsMeaning": "1 bps = 0.01% of the order value.",
            },
        }

    @tool(
        "hydromancer_list_markets",
        "List every market and HIP-3 exchange",
        "Lists every tradable perp market, optionally filtered by search text or category (crypto, stocks, indices, "
        "commodities, fx), plus the HIP-3 exchanges (perp DEXs) that list stocks and other assets. Use when unsure of a "
        "market's exact name.",
    )
    async def list_markets(
        search: Annotated[Optional[str], Field(description='Text to search for, e.g. "tsla", "gold", "pepe".')] = None,
        category: Optional[Literal["crypto", "stocks", "indices", "commodities", "fx"]] = None,
        limit: Optional[int] = _limit(100, 2000),
    ) -> Obj:
        async def optional(body: Obj) -> Any:
            try:
                return await client.info(body)
            except HydromancerError:
                return []

        mids, cats, dexs = await asyncio.gather(
            client.info({"type": "allMids", "dex": "ALL_DEXS"}),
            optional({"type": "perpCategories"}),
            optional({"type": "perpDexs"}),
        )
        cat_map = {str(p[0]): p[1] for p in (cats if isinstance(cats, list) else []) if isinstance(p, list) and len(p) >= 2}
        rows = [
            {
                "market": k,
                "exchange": k.split(":", 1)[0] if ":" in k else "hyperliquid",
                "category": cat_map.get(k, None if ":" in k else "crypto"),
                "midPrice": num(v),
            }
            for k, v in (mids.items() if isinstance(mids, dict) else [])
            if not k.startswith(("@", "#"))
        ]
        if search:
            rows = [r for r in rows if search.lower() in r["market"].lower()]
        if category:
            rows = [r for r in rows if r["category"] == category or (category == "fx" and r["category"] == "currencies")]
        rows.sort(key=lambda r: r["market"])
        return {
            "hip3Exchanges": [
                {"name": d.get("name"), "fullName": d.get("fullName"), "markets": len(d.get("assetToStreamingOiCap") or [])}
                for d in _rows(dexs)
            ],
            "totalMatches": len(rows),
            "markets": rows[: limit or 100],
        }

    # ---------- Account / advanced ----------
    @tool(
        "hydromancer_my_usage",
        "My Hydromancer API usage",
        "Your own Hydromancer API usage by day and request type: requests, failures, speed and billable tokens.",
    )
    async def my_usage(days: Annotated[Optional[int], Field(ge=1, le=90, description="Days to look back (default 7).")] = None) -> Obj:
        rows = _rows(await client.info({"type": "apiUsage", "days": days or 7}))
        return {
            "days": days or 7,
            "totalRequests": _whole(sum(num(r.get("total_requests")) for r in rows)),
            "tokensConsumed": _whole(sum(num(r.get("tokens_consumed")) for r in rows)),
            "rows": rows,
        }

    @tool(
        "hydromancer_raw_request",
        "Advanced: any Hydromancer API request",
        "Send any Hydromancer /info request directly, for endpoints the other tools do not cover (e.g. vaultSummaries, "
        "historicalOrders, spotMeta, outcomeMeta, delegations, perpDexStatus, userFunding, openInterestHistory). Pass the "
        'request body as JSON including "type". Full list: https://docs.hydromancer.xyz/llms.txt . Read-only: /info cannot '
        "place orders. Binary snapshot endpoints are not supported.",
    )
    async def raw_request(
        body: Annotated[
            dict[str, Any],
            Field(description='Request body, e.g. {"type":"vaultSummaries"} or {"type":"historicalOrders","user":"0x..."}.'),
        ],
        limit: Optional[int] = _limit(200, 5000),
    ) -> Any:
        if not isinstance(body.get("type"), str) or not body["type"]:
            raise ValueError('The body must include a "type" field, e.g. {"type":"meta"}.')
        res = await client.info(body)
        if isinstance(res, list):
            chunk, paging = page(res, limit or 200)
            return {"paging": paging, "rows": chunk}
        return res

    return names
