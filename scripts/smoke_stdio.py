"""Real stdio smoke test: start `hydromancer-mcp` as a subprocess, list its tools and call two live.

Needs HYDROMANCER_API_KEY in the environment, e.g. `scripts/with_key.sh uv run python scripts/smoke_stdio.py`.
Exits non-zero on any failure. Never prints the key.
"""

import asyncio
import json
import os
import shutil
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _text(result: object) -> str:
    return "".join(getattr(c, "text", "") for c in getattr(result, "content", []))


def _is_error(result: object) -> bool:
    return bool(getattr(result, "is_error", False) or getattr(result, "isError", False))


async def smoke() -> int:
    key = os.environ.get("HYDROMANCER_API_KEY", "")
    if not key:
        print("HYDROMANCER_API_KEY is not set", file=sys.stderr)
        return 2
    exe = shutil.which("hydromancer-mcp")
    command, args = (exe, []) if exe else (sys.executable, ["-m", "hydromancer_mcp"])
    params = StdioServerParameters(
        command=command,
        args=args,
        env={"HYDROMANCER_API_KEY": key, "PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        names = sorted(t.name for t in tools)
        print(f"{len(names)} tools: {', '.join(names)}")
        if len(names) != 12:
            print("expected 12 tools", file=sys.stderr)
            return 1

        snap = await session.call_tool("hydromancer_market_snapshot", {"coins": ["$btc", "eth-perp", "xyz:tsla"]})
        if _is_error(snap):
            print("market_snapshot failed:", _text(snap), file=sys.stderr)
            return 1
        markets = json.loads(_text(snap))["markets"]
        print("market_snapshot:", [(m["coin"], m.get("markPrice")) for m in markets])
        if [m["coin"] for m in markets] != ["BTC", "ETH", "xyz:TSLA"] or not all(m.get("markPrice") for m in markets):
            print("unexpected market_snapshot result", file=sys.stderr)
            return 1

        hist = await session.call_tool("hydromancer_funding_history", {"coin": "eth", "since": "24h"})
        if _is_error(hist):
            print("funding_history failed:", _text(hist), file=sys.stderr)
            return 1
        data = json.loads(_text(hist))
        print(f"funding_history: {data['coin']} periods={data['periods']} annualizedPct={data['annualizedPct']}")
        if data["coin"] != "ETH" or data["periods"] < 1:
            print("unexpected funding_history result", file=sys.stderr)
            return 1

        bad = await session.call_tool("hydromancer_wallet_trades", {"address": "0x123"})
        if not _is_error(bad):
            print("invalid address was not reported as an error", file=sys.stderr)
            return 1
        print("invalid input -> isError: ok")
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(smoke()))
