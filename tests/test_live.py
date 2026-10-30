"""Opt-in live tests against the real Hydromancer API.

Run with: HYDROMANCER_LIVE=1 HYDROMANCER_API_KEY=... uv run pytest -m live
They make a few dozen small read-only requests.
"""

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from hydromancer_mcp.client import HydromancerClient
from hydromancer_mcp.server import create_server

from .conftest import call, call_json

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("HYDROMANCER_LIVE") != "1" or not os.environ.get("HYDROMANCER_API_KEY"),
        reason="live tests need HYDROMANCER_LIVE=1 and HYDROMANCER_API_KEY",
    ),
]


@pytest.fixture
def live() -> Any:
    return create_server(HydromancerClient())[0]


async def test_market_snapshot_friendly_names(live: Any) -> None:
    out = await call_json(live, "hydromancer_market_snapshot", {"coins": ["btc", "$ETH", "ETH-PERP", "xyz:tsla"]})
    assert [m["coin"] for m in out["markets"]] == ["BTC", "ETH", "ETH", "xyz:TSLA"]
    assert all(m["markPrice"] > 0 for m in out["markets"])


async def test_market_snapshot_top(live: Any) -> None:
    out = await call_json(live, "hydromancer_market_snapshot", {"top": 5})
    assert len(out["markets"]) == 5 and out["markets"][0]["volume24hUsd"] > 0


async def test_price_history(live: Any) -> None:
    out = await call_json(live, "hydromancer_price_history", {"coin": "eth", "interval": "15m", "since": "3h"})
    assert out["coin"] == "ETH" and 8 <= out["candles"] <= 14 and out["summary"]["high"] >= out["summary"]["low"]


async def test_funding_history(live: Any) -> None:
    out = await call_json(live, "hydromancer_funding_history", {"coin": "BTC", "since": "2d"})
    assert out["coin"] == "BTC" and out["periods"] >= 24


async def test_market_depth(live: Any) -> None:
    out = await call_json(live, "hydromancer_market_depth", {"coin": "btc", "orderSizeUsd": 100000, "since": "2h"})
    assert isinstance(out["depth"], list) and out["depth"][0]["bidDepthUsd"] > 0


async def test_recent_liquidations(live: Any) -> None:
    out = await call_json(live, "hydromancer_recent_liquidations", {"since": "24h", "limit": 3})
    assert out["totals"]["liquidations"] >= len(out["liquidations"])


async def test_list_markets(live: Any) -> None:
    out = await call_json(live, "hydromancer_list_markets", {"search": "tsla"})
    assert any(m["market"] == "xyz:TSLA" for m in out["markets"]) and out["hip3Exchanges"]


async def test_trader_flow(live: Any) -> None:
    top = await call_json(live, "hydromancer_top_traders", {"period": "7d", "limit": 3})
    assert top["traders"], top
    wallet = top["traders"][0]["address"]
    overview = await call_json(live, "hydromancer_wallet_overview", {"address": wallet, "includeHip3": True})
    assert overview["address"] == wallet and "balanceUsd" in overview and overview["tradingRecord"]
    trades = await call_json(live, "hydromancer_wallet_trades", {"address": wallet, "since": "7d", "limit": 5})
    assert trades["totals"]["fills"] >= 1
    closed = await call_json(live, "hydromancer_wallet_closed_trades", {"address": wallet, "since": "30d", "limit": 20})
    assert closed["summary"]["trades"] >= 1


async def test_my_usage(live: Any) -> None:
    out = await call_json(live, "hydromancer_my_usage", {"days": 1})
    assert out["totalRequests"] >= 1


async def test_raw_request(live: Any) -> None:
    out = await call_json(live, "hydromancer_raw_request", {"body": {"type": "perpDexs"}, "limit": 2})
    assert out["paging"]["returned"] == 2


async def test_api_error_is_readable(live: Any) -> None:
    is_error, text = await call(live, "hydromancer_raw_request", {"body": {"type": "noSuchEndpoint"}})
    assert is_error and "HTTP 400" in text and os.environ["HYDROMANCER_API_KEY"] not in text


def test_stdio_smoke() -> None:
    script = Path(__file__).resolve().parent.parent / "scripts" / "smoke_stdio.py"
    if not script.exists():
        pytest.skip("smoke script is not shipped in this distribution")
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SMOKE OK" in proc.stdout
    assert os.environ["HYDROMANCER_API_KEY"] not in proc.stdout + proc.stderr
