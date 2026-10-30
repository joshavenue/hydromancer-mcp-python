import json
from typing import Any

import httpx
import pytest
import respx

from .conftest import API, TEST_KEY, bodies, call, call_json

WALLET = "0x" + "ab" * 20
MIDS = {"BTC": "77000", "ETH": "2200", "xyz:TSLA": "394"}

EXPECTED_TOOLS = {
    "hydromancer_wallet_overview",
    "hydromancer_wallet_trades",
    "hydromancer_wallet_closed_trades",
    "hydromancer_top_traders",
    "hydromancer_market_snapshot",
    "hydromancer_price_history",
    "hydromancer_funding_history",
    "hydromancer_recent_liquidations",
    "hydromancer_market_depth",
    "hydromancer_list_markets",
    "hydromancer_my_usage",
    "hydromancer_raw_request",
}


def by_type(responses: dict[str, Any]) -> Any:
    """respx side effect that answers by request "type"."""

    def handler(request: httpx.Request) -> httpx.Response:
        kind = json.loads(request.content)["type"]
        value = responses[kind]
        return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)

    return handler


async def test_tool_list_and_annotations(server: Any) -> None:
    tools = await server.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
    for t in tools:
        dumped = t.model_dump(by_alias=True, exclude_none=True)
        assert dumped["annotations"]["readOnlyHint"] is True
        assert dumped["annotations"]["destructiveHint"] is False
        assert dumped["description"]


@respx.mock
async def test_wallet_trades(server: Any) -> None:
    fills = [
        {"time": 1_790_812_800_000, "coin": "ETH", "dir": "Open Long", "px": "2000", "sz": "1", "closedPnl": "0", "fee": "1", "crossed": True},
        {"time": 1_790_812_900_000, "coin": "BTC", "dir": "Open Short", "px": "70000", "sz": "0.1", "closedPnl": "0", "fee": "2", "crossed": False},
        {"time": 1_790_813_000_000, "coin": "ETH", "dir": "Close Long", "px": "2100", "sz": "1", "closedPnl": "100", "fee": "1", "crossed": True},
    ]
    route = respx.post(API).mock(side_effect=by_type({"userFillsByTime": fills, "allMids": MIDS}))
    out = await call_json(server, "hydromancer_wallet_trades", {"address": WALLET, "since": "2026-10-01", "until": "2026-10-02", "coin": "$eth"})
    sent = [b for b in bodies(route) if b["type"] == "userFillsByTime"][0]
    assert sent == {"type": "userFillsByTime", "user": WALLET, "startTime": 1_790_812_800_000, "endTime": 1_790_899_200_000, "aggregateByTime": True}
    assert out["window"] == {"from": "2026-10-01T00:00:00.000Z", "to": "2026-10-02T00:00:00.000Z"}
    assert out["totals"]["fills"] == 2 and out["totals"]["realizedPnlUsd"] == 100 and out["totals"]["volumeUsd"] == 4100
    assert [f["action"] for f in out["fills"]] == ["Close Long", "Open Long"]  # newest first
    assert out["paging"] == {"total": 2, "offset": 0, "returned": 2, "hasMore": False}
    assert "note" not in out


@respx.mock
async def test_wallet_trades_paging_and_cap_note(server: Any) -> None:
    fills = [{"time": 1_790_812_800_000 + i, "coin": "BTC", "px": "1", "sz": "1"} for i in range(2000)]
    respx.post(API).mock(side_effect=by_type({"userFillsByTime": fills}))
    out = await call_json(server, "hydromancer_wallet_trades", {"address": WALLET, "limit": 5, "offset": 10, "mergeSplitOrders": False})
    assert out["paging"] == {"total": 2000, "offset": 10, "returned": 5, "hasMore": True}
    assert "2,000-fill maximum" in out["note"]


async def test_invalid_address_is_rejected(server: Any) -> None:
    is_error, text = await call(server, "hydromancer_wallet_trades", {"address": "0x123"})
    assert is_error
    assert "address" in text.lower() or "pattern" in text.lower()


async def test_bad_time_is_a_readable_error(server: Any) -> None:
    is_error, text = await call(server, "hydromancer_wallet_trades", {"address": WALLET, "since": "last tuesday"})
    assert is_error and "Could not understand the time" in text


async def test_until_before_since(server: Any) -> None:
    is_error, text = await call(server, "hydromancer_funding_history", {"coin": "BTC", "since": "2026-10-02", "until": "2026-10-01"})
    assert is_error and "`until` must be later" in text


@respx.mock
async def test_wallet_overview(server: Any) -> None:
    portfolio = {
        "userAbstraction": "default",
        "clearinghouseState": {
            "marginSummary": {"accountValue": "1000", "totalNtlPos": "3000", "totalMarginUsed": "300"},
            "withdrawable": "700",
            "assetPositions": [
                {"position": {"coin": "ETH", "szi": "-1.5", "positionValue": "3000", "entryPx": "2100", "liquidationPx": None,
                              "leverage": {"type": "cross", "value": 5}, "unrealizedPnl": "-150", "returnOnEquity": "-0.25",
                              "cumFunding": {"sinceOpen": "-4"}}}
            ],
        },
        "spotClearinghouseState": {"balances": [{"coin": "USDC", "total": "50", "hold": "0", "entryNtl": "0"}, {"coin": "HYPE", "total": "0"}]},
    }
    pnl = {"totalPnl": "1234.567", "winRate": 0.6, "totalTrades": 10, "humanScore": 80}
    route = respx.post(API).mock(side_effect=by_type({"portfolioState": portfolio, "userPnlSummary": pnl}))
    out = await call_json(server, "hydromancer_wallet_overview", {"address": WALLET})
    assert {"type": "portfolioState", "user": WALLET} in bodies(route)  # no dex unless includeHip3
    assert out["balanceUsd"] == 1050
    pos = out["perp"]["positions"][0]
    assert pos["side"] == "short" and pos["size"] == 1.5 and pos["leverage"] == "5x cross" and pos["liquidationPrice"] is None
    assert pos["fundingSinceOpenUsd"] == 4
    assert out["perp"]["effectiveLeverage"] == 3
    assert out["spotBalances"] == [{"token": "USDC", "amount": 50, "onHold": 0, "costBasisUsd": 0}]
    assert out["tradingRecord"]["realizedPnlUsd"] == 1234.57 and out["tradingRecord"]["winRatePct"] == 60


@respx.mock
async def test_wallet_overview_unified_account_and_hip3(server: Any) -> None:
    portfolio = {
        "userAbstraction": "unifiedAccount",
        "clearinghouseState": {
            "native": {"marginSummary": {"accountValue": "10"}, "assetPositions": []},
            "xyz": {"marginSummary": {"accountValue": "5"}, "assetPositions": [{"position": {"coin": "xyz:TSLA", "szi": "2", "positionValue": "800"}}]},
            "flx": {"marginSummary": {"accountValue": "0"}, "assetPositions": []},
        },
        "spotClearinghouseState": {"balances": [{"coin": "USDC", "total": "500"}, {"coin": "USDT0", "total": "25"}]},
    }
    route = respx.post(API).mock(side_effect=by_type({"portfolioState": portfolio, "userPnlSummary": httpx.Response(500, text="x")}))
    out = await call_json(server, "hydromancer_wallet_overview", {"address": WALLET, "includeHip3": True})
    assert {"type": "portfolioState", "user": WALLET, "dex": "ALL_DEXES"} in bodies(route)
    assert out["balanceUsd"] == 525 and out["accountMode"] == "unifiedAccount"
    assert set(out["perp"]) == {"native", "xyz"}
    assert "server error" in out["tradingRecord"]["error"]


@respx.mock
async def test_top_traders_request(server: Any) -> None:
    route = respx.post(API).mock(side_effect=by_type({"userPnlLeaderboard": {"total": 2, "users": [{"user": WALLET, "totalPnl": "10", "winRate": 0.5}]}}))
    out = await call_json(server, "hydromancer_top_traders", {"period": "7d", "rankBy": "volume", "limit": 1, "offset": 4, "market": "main"})
    assert bodies(route)[0] == {"type": "userPnlLeaderboard", "window": "7d", "sortBy": "volumeTraded", "limit": 1, "offset": 4, "minHumanScore": 60, "dex": "main_dex"}
    assert out["traders"][0]["rank"] == 5 and out["traders"][0]["winRatePct"] == 50
    await call_json(server, "hydromancer_top_traders", {"excludeBots": False})
    assert "minHumanScore" not in bodies(route)[1]


@respx.mock
async def test_market_snapshot_resolves_friendly_names(server: Any) -> None:
    ctx = {"ETH": {"markPx": "2000", "oraclePx": "2001", "midPx": "2000.5", "dayNtlVlm": "1e9", "openInterest": "10"}}
    route = respx.post(API).mock(side_effect=by_type({"allMids": MIDS, "assetContext": ctx}))
    out = await call_json(server, "hydromancer_market_snapshot", {"coins": ["eth-perp", "xyz:tsla"]})
    assert [b for b in bodies(route) if b["type"] == "assetContext"][0]["coins"] == ["ETH", "xyz:TSLA"]
    assert out["markets"][0]["openInterestUsd"] == 20000
    assert "Unknown market" in out["markets"][1]["note"]


@respx.mock
async def test_market_snapshot_top_markets(server: Any) -> None:
    meta = [{"universe": [{"name": "BTC", "maxLeverage": 40}, {"name": "OLD", "isDelisted": True}, {"name": "ETH", "maxLeverage": 25}]},
            [{"markPx": "1", "dayNtlVlm": "5"}, {"markPx": "1", "dayNtlVlm": "999"}, {"markPx": "1", "dayNtlVlm": "50"}]]
    respx.post(API).mock(side_effect=by_type({"metaAndAssetCtxs": meta}))
    out = await call_json(server, "hydromancer_market_snapshot", {"top": 5})
    assert [m["coin"] for m in out["markets"]] == ["ETH", "BTC"]


@respx.mock
async def test_price_history(server: Any) -> None:
    candles = [{"t": 1_790_812_800_000, "o": "100", "h": "120", "l": "90", "c": "110", "v": "2", "n": 5},
               {"t": 1_790_816_400_000, "o": "110", "h": "130", "l": "105", "c": "121", "v": "1", "n": 3}]
    route = respx.post(API).mock(side_effect=by_type({"allMids": MIDS, "candleSnapshot": candles}))
    out = await call_json(server, "hydromancer_price_history", {"coin": "BTCUSDT", "interval": "1h", "since": 1_790_812_800, "until": "2026-10-01T02:00:00Z"})
    req = [b for b in bodies(route) if b["type"] == "candleSnapshot"][0]["req"]
    assert req == {"coin": "BTC", "interval": "1h", "startTime": 1_790_812_800_000, "endTime": 1_790_820_000_000}
    assert out["summary"] == {"open": 100, "close": 121, "high": 130, "low": 90, "changePct": 21, "volumeUsd": 341}


@respx.mock
async def test_funding_history(server: Any) -> None:
    rows = [{"time": 1_790_812_800_000 + i * 3_600_000, "fundingRate": "0.0000125"} for i in range(30)]
    respx.post(API).mock(side_effect=by_type({"allMids": MIDS, "fundingHistory": rows}))
    out = await call_json(server, "hydromancer_funding_history", {"coin": "btc"})
    assert out["coin"] == "BTC" and out["periods"] == 30 and len(out["latest"]) == 24
    assert out["averageHourlyPct"] == 0.00125 and out["annualizedPct"] == 10.95


@respx.mock
async def test_recent_liquidations(server: Any) -> None:
    rows = [{"time": 1, "coin": "ETH", "side": "A", "px": "2000", "sz": "1", "user": WALLET},
            {"time": 2, "coin": "BTC", "side": "B", "px": "70000", "sz": "0.1", "liquidation": {"liquidatedUser": WALLET}}]
    route = respx.post(API).mock(side_effect=by_type({"liquidationHistoryByTime": rows}))
    out = await call_json(server, "hydromancer_recent_liquidations", {"since": "6h"})
    sent = bodies(route)[0]
    assert "coin" not in sent and sent["limit"] == 1000
    assert out["totals"]["longsLiquidatedUsd"] == 2000 and out["totals"]["shortsLiquidatedUsd"] == 7000
    assert out["liquidations"][0]["coin"] == "BTC" and out["liquidations"][0]["address"] == WALLET


@respx.mock
async def test_market_depth(server: Any) -> None:
    depth = {"timestampMs": 1_790_812_800_000, "data": [
        {"coin": "TSLA", "dex": "io", "levels": [{"bps": 10, "bidDepthUsd": 1, "askDepthUsd": 1}]},
        {"coin": "TSLA", "dex": "xyz", "levels": [{"bps": 10, "bidDepthUsd": 1000.123, "askDepthUsd": 900}]},
        {"coin": "BTC", "dex": "hyperliquid", "levels": [{"bps": 10, "bidDepthUsd": 5e7, "askDepthUsd": 4e7}]},
    ]}
    slip = [{"timestamp": 1_790_812_800_000, "buySlippageBps": 2, "sellSlippageBps": 4}]
    route = respx.post(API).mock(side_effect=by_type({"allMids": MIDS, "currentDepth": depth, "slippageHistory": slip}))
    out = await call_json(server, "hydromancer_market_depth", {"coin": "xyz:tsla", "orderSizeUsd": 50000})
    assert [b for b in bodies(route) if b["type"] == "slippageHistory"][0]["amount"] == 50000
    assert out["depth"] == [{"withinPct": 0.1, "bidDepthUsd": 1000.12, "askDepthUsd": 900}]
    assert out["slippage"]["averageBuyBps"] == 2
    btc = await call_json(server, "hydromancer_market_depth", {"coin": "btc"})
    assert btc["depth"][0]["bidDepthUsd"] == 5e7
    none = await call_json(server, "hydromancer_market_depth", {"coin": "ETH"})
    assert none["depth"] == "No depth data for this market."


@respx.mock
async def test_list_markets(server: Any) -> None:
    respx.post(API).mock(side_effect=by_type({
        "allMids": {**MIDS, "@1": "1"},
        "perpCategories": [["xyz:TSLA", "stocks"]],
        "perpDexs": [None, {"name": "xyz", "fullName": "XYZ", "assetToStreamingOiCap": [["xyz:TSLA", "1"]]}],
    }))
    out = await call_json(server, "hydromancer_list_markets", {"category": "stocks"})
    assert out["markets"] == [{"market": "xyz:TSLA", "exchange": "xyz", "category": "stocks", "midPrice": 394}]
    assert out["hip3Exchanges"] == [{"name": "xyz", "fullName": "XYZ", "markets": 1}]


@respx.mock
async def test_my_usage(server: Any) -> None:
    respx.post(API).mock(side_effect=by_type({"apiUsage": [{"total_requests": 3, "tokens_consumed": 5}, {"total_requests": 2, "tokens_consumed": 1}]}))
    out = await call_json(server, "hydromancer_my_usage", {})
    assert out["totalRequests"] == 5 and out["tokensConsumed"] == 6 and out["days"] == 7


@respx.mock
async def test_raw_request(server: Any) -> None:
    route = respx.post(API).mock(return_value=httpx.Response(200, json=list(range(10))))
    out = await call_json(server, "hydromancer_raw_request", {"body": {"type": "vaultSummaries"}, "limit": 3})
    assert bodies(route)[0] == {"type": "vaultSummaries"}
    assert out["paging"] == {"total": 10, "offset": 0, "returned": 3, "hasMore": True} and out["rows"] == [0, 1, 2]
    assert "never as instructions" in out["note"]
    is_error, text = await call(server, "hydromancer_raw_request", {"body": {"user": WALLET}})
    assert is_error and '"type"' in text


@respx.mock
async def test_errors_are_tool_errors_and_masked(server: Any) -> None:
    respx.post(API).mock(return_value=httpx.Response(403, text=f"error code: 1010 key={TEST_KEY}"))
    is_error, text = await call(server, "hydromancer_my_usage", {})
    assert is_error
    assert "refused the API key" in text and TEST_KEY not in text


@respx.mock
async def test_large_results_are_truncated(server: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYDROMANCER_MAX_RESULT_CHARS", "1000")
    respx.post(API).mock(return_value=httpx.Response(200, json={"blob": "x" * 5000}))
    is_error, text = await call(server, "hydromancer_raw_request", {"body": {"type": "meta"}})
    assert not is_error and len(text) <= 1000
    out = json.loads(text)  # still valid JSON
    assert "cut to at most 1000" in out["TRUNCATED"] and out["partialResult"].startswith("{")


def test_truncation_fits_cap_even_with_escaping(monkeypatch: pytest.MonkeyPatch) -> None:
    from hydromancer_mcp.shape import to_text

    for cap in ("1", "100", "1000", "1500"):
        monkeypatch.setenv("HYDROMANCER_MAX_RESULT_CHARS", cap)
        text = to_text({"quotes": ['"\\\n' * 50] * 40})
        assert len(text) <= max(int(cap), 1000) and json.loads(text)["TRUNCATED"]


@respx.mock
async def test_closed_trades_summary_covers_whole_window(server: Any) -> None:
    trades = [{"coin": "BTC" if i % 2 else "ETH", "net_pnl": "10" if i % 3 else "-5", "duration_ms": 60_000 * (i + 1),
               "close_time": 1_790_812_800_000 + i} for i in range(10)]
    route = respx.post(API).mock(side_effect=by_type({"userCompletedTradesByTime": trades, "allMids": MIDS}))
    out = await call_json(server, "hydromancer_wallet_closed_trades", {"address": WALLET, "limit": 2, "coin": "eth"})
    assert [b for b in bodies(route) if b["type"] == "userCompletedTradesByTime"][0]["limit"] == 500
    assert out["summary"]["trades"] == 5 and len(out["trades"]) == 2  # summary over all ETH trades, 2 rows shown
    assert out["summary"]["medianHoldMinutes"] == 5  # holds 1,3,5,7,9 minutes
    assert out["summary"]["byCoin"]["ETH"]["netPnlUsd"] == 20
    assert out["trades"][0]["closed"] > out["trades"][1]["closed"]  # newest first
    assert "note" not in out


@respx.mock
async def test_closed_trades_median_even_and_cap_note(server: Any) -> None:
    trades = [{"coin": "BTC", "net_pnl": "1", "duration_ms": 60_000 * (1 if i < 250 else 3), "close_time": i} for i in range(500)]
    respx.post(API).mock(side_effect=by_type({"userCompletedTradesByTime": trades}))
    out = await call_json(server, "hydromancer_wallet_closed_trades", {"address": WALLET})
    assert out["summary"]["medianHoldMinutes"] == 2
    assert "per-request maximum" in out["note"]


async def test_non_finite_time_is_rejected(server: Any) -> None:
    is_error, text = await call(server, "hydromancer_funding_history", {"coin": "BTC", "since": 1e300})
    assert is_error and "Could not understand the time" in text


def test_non_finite_numbers_become_null() -> None:
    from hydromancer_mcp.shape import to_text

    assert json.loads(to_text({"a": float("nan"), "b": [float("inf"), 1.5], "c": "x"})) == {"a": None, "b": [None, 1.5], "c": "x"}
    assert to_text(float("-inf")) == "null"


def test_snap_version_matches_package() -> None:
    import re
    from pathlib import Path

    from hydromancer_mcp import __version__

    snap = Path(__file__).resolve().parent.parent / "snap" / "snapcraft.yaml"
    if not snap.exists():
        pytest.skip("snapcraft.yaml is not shipped in this distribution")
    match = re.search(r'^version:\s*"([^"]+)"', snap.read_text(), re.MULTILINE)
    assert match and match.group(1) == __version__
