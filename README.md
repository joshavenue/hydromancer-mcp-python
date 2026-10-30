# hydromancer-mcp

Read-only [MCP](https://modelcontextprotocol.io) server for the [Hydromancer](https://hydromancer.xyz) Hyperliquid API, in Python. Ask your AI app about wallets, top traders, markets, funding and liquidations in plain English.

> **Disclosure:** this package is maintained by the Hydromancer team. You need a Hydromancer API key to use it.

```bash
uvx hydromancer-mcp
```

> - "What is this wallet holding, and is it a good trader?"
> - "Who were the best traders this week, without bots?"
> - "Who got liquidated in the last 6 hours?"
> - "How much BTC can I sell before the price moves 0.5%?"

- **12 read-only tools**: wallets, trades, top traders, markets, funding, depth, liquidations, your API usage, plus a raw request tool for other `/info` endpoints
- **Friendly inputs**: coin names in any case (`btc`, `$ETH`, `ETH-PERP`, `xyz:tsla`), times like `24h`, `7d` or `2026-10-01`
- **Read-only**: it cannot place, cancel or sign orders, and it never handles funds or private keys
- **Few dependencies**: besides the MCP SDK it uses only `httpx` and `pydantic`; Python 3.10+

## Hosts contacted

The server makes HTTPS requests to one of these hosts:

| Host | When |
|---|---|
| `api.hydromancer.xyz` | Every tool call (default) |
| `api-testnet.hydromancer.xyz` | Instead of the above, only if you set `HYDROMANCER_BASE_URL` to the testnet URL |

Any other `HYDROMANCER_BASE_URL` is refused at startup, so your key cannot be sent elsewhere. Redirects are not followed. No telemetry. (Installing with `uvx` or `pip` downloads the package from PyPI; the server itself never contacts PyPI.)

## 1. Get a Hydromancer API key

See [Get API keys](https://docs.hydromancer.xyz/readme/get-api-keys).

## 2. Add it to your AI app

You need [uv](https://docs.astral.sh/uv/getting-started/installation/) (for `uvx`). Replace `YOUR_KEY` with your key.

### Claude Desktop

Settings → Developer → Edit Config, then add:

```json
{
  "mcpServers": {
    "hydromancer": {
      "command": "uvx",
      "args": ["hydromancer-mcp"],
      "env": { "HYDROMANCER_API_KEY": "YOUR_KEY" }
    }
  }
}
```

Restart Claude Desktop. If it cannot find `uvx`, use the full path from `which uvx` as the `command`.

### Claude Code

```bash
claude mcp add hydromancer -e HYDROMANCER_API_KEY=YOUR_KEY -- uvx hydromancer-mcp
```

### Cursor

`~/.cursor/mcp.json` (all projects) or `.cursor/mcp.json` (one project):

```json
{
  "mcpServers": {
    "hydromancer": {
      "command": "uvx",
      "args": ["hydromancer-mcp"],
      "env": { "HYDROMANCER_API_KEY": "YOUR_KEY" }
    }
  }
}
```

### Codex

`~/.codex/config.toml`:

```toml
[mcp_servers.hydromancer]
command = "uvx"
args = ["hydromancer-mcp"]
env = { HYDROMANCER_API_KEY = "YOUR_KEY" }
```

### Other MCP apps, or pip

Any app that runs stdio MCP servers works with `uvx hydromancer-mcp`. Without uv:

```bash
pip install hydromancer-mcp
hydromancer-mcp --help
```

## 3. Ask

- `Give me an overview of wallet 0x… and tell me if it's a good trader.`
- `Show the top 10 traders by profit over the last 7 days, no bots, then what the top 3 hold right now.`
- `Who got liquidated on Hyperliquid in the last 6 hours? Longs or shorts?`
- `BTC, ETH and HYPE: price, 24h volume and open interest.`
- `What's the BTC funding rate averaged over the last week, annualized?`
- `How deep is the xyz:TSLA order book within 1% of the price?`

## Tools

| Tool | What it answers |
|---|---|
| `hydromancer_wallet_overview` | Balance (handles unified-account and portfolio-margin wallets), open positions with entry, liquidation price, leverage and PnL, spot balances, trading record, human score (low suggests a bot) |
| `hydromancer_wallet_trades` | Fills in a time window, with volume, PnL, fees and per-coin totals |
| `hydromancer_wallet_closed_trades` | Round-trip trades: win rate, net PnL, hold time, best coin |
| `hydromancer_top_traders` | Leaderboard by profit, win rate or volume, with bot and activity filters |
| `hydromancer_market_snapshot` | Price, 24h volume, open interest; max leverage in the top-markets list |
| `hydromancer_price_history` | Candles from 1 second to 1 month |
| `hydromancer_funding_history` | Funding rates, average and annualized |
| `hydromancer_recent_liquidations` | Liquidations by market, with long/short totals |
| `hydromancer_market_depth` | Order book depth and slippage for a trade size |
| `hydromancer_list_markets` | Every market and HIP-3 exchange, searchable by name or category |
| `hydromancer_my_usage` | Your own API usage |
| `hydromancer_raw_request` | Any other Hydromancer `/info` request, e.g. open orders, transfers, vaults ([endpoint list](https://docs.hydromancer.xyz/llms.txt)) |

The server also provides a `hydromancer_guide` prompt and a `hydromancer://guide` resource that tell the AI which tool to use for which question.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `HYDROMANCER_API_KEY` | (required) | Your API key |
| `HYDROMANCER_BASE_URL` | `https://api.hydromancer.xyz/info` | Set to `https://api-testnet.hydromancer.xyz/info` for testnet; any other host is refused |
| `HYDROMANCER_TIMEOUT_MS` | `30000` | Request timeout |
| `HYDROMANCER_MAX_RESULT_CHARS` | `40000` | Cap on one tool result (minimum 1000), so the AI is not flooded |

## Security

- **Read-only.** Every request goes to the `/info` endpoint, which only returns data.
- **What is sent.** Only the requests needed for each tool call (the wallet addresses, coin names and time ranges you ask about, plus a market-list lookup to resolve coin names), your key, and a User-Agent naming this package and its version, to the hosts above.
- **Your key stays local.** It is read from the environment, sent only in the `Authorization` header to the hosts above, never logged or printed, and replaced with `***` if it ever appears in an error message.
- **Responses are data, not instructions.** API responses (market names, descriptions, any text field) are returned to the AI as data. The server tells the AI to treat them as data and never to follow instructions found inside them, and `hydromancer_raw_request` results carry the same note. This is guidance to the AI, not a guarantee, so review what your AI app does with the results.
- **Rate limits.** On HTTP 429 the server retries at most twice, waiting as long as the API asks (the `Retry-After` header or the response body, otherwise 1 s then 2 s, plus a fraction of a second), but only if that wait is 10 seconds or less. Otherwise it tells the AI how long to wait.
- **Size limits.** Responses over 64 MB are refused, and each tool result is capped (see `HYDROMANCER_MAX_RESULT_CHARS`) with a note when it is cut.

## Good to know

- The data is for information only and is not financial advice.
- Each tool call makes one or more ordinary API requests (some tools combine several, and coin names are checked against the market list), and each request counts toward your key's usage. Check it with `hydromancer_my_usage`.
- HIP-3 markets use a prefix, for example `xyz:TSLA`.
- Balances: unified-account and portfolio-margin wallets keep their money in spot, so `balanceUsd` uses their spot stablecoins. Other spot tokens are listed but not priced.
- Times are UTC. Some endpoints cap a single request (for example 2,000 fills); the tool says so when it happens.
- Binary snapshot endpoints are not supported.

## Development

```bash
uv sync
uv run pytest                              # offline unit tests (HTTP mocked with respx)
HYDROMANCER_LIVE=1 HYDROMANCER_API_KEY=... uv run pytest -m live   # opt-in live tests
HYDROMANCER_API_KEY=... uv run python scripts/smoke_stdio.py       # real stdio round trip
```

## License

MIT © Hydromancer BV
