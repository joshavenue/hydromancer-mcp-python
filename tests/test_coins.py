import httpx
import pytest
import respx

from hydromancer_mcp.client import HydromancerClient
from hydromancer_mcp.coins import CoinResolver, candidates

from .conftest import API, bodies

MIDS = {"BTC": "77000", "ETH": "2200", "kPEPE": "0.01", "HYPE": "40", "xyz:TSLA": "394", "@107": "40", "#20": "0.5"}


def test_candidates() -> None:
    assert candidates("$ETH") == ["ETH"]
    assert candidates("ETH-PERP") == ["ETH-PERP", "ETH"]
    assert candidates("ethusdt") == ["ethusdt", "eth"]
    assert candidates("BTC/USDC") == ["BTC/USDC", "BTC"]
    assert candidates("  xyz:tsla ") == ["xyz:tsla"]
    assert candidates("USDC") == ["USDC"]


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("btc", "BTC"),
        ("$ETH", "ETH"),
        ("ETH-PERP", "ETH"),
        ("ETHUSDT", "ETH"),
        ("eth_perp", "ETH"),
        ("kpepe", "kPEPE"),
        ("xyz:tsla", "xyz:TSLA"),
        ("XYZ:TSLA", "xyz:TSLA"),
        ("  hype  ", "HYPE"),
        ("NOTACOIN", "NOTACOIN"),  # unknown names pass through
        ("@107", "@107"),  # spot ids are not market names
    ],
)
@respx.mock
async def test_resolve(client: HydromancerClient, typed: str, expected: str) -> None:
    respx.post(API).mock(return_value=httpx.Response(200, json=MIDS))
    assert await CoinResolver(client).resolve(typed) == expected


@respx.mock
async def test_market_list_is_cached_and_requested_correctly(client: HydromancerClient) -> None:
    route = respx.post(API).mock(return_value=httpx.Response(200, json=MIDS))
    resolver = CoinResolver(client)
    assert await resolver.resolve_many(["btc", "eth", "xyz:tsla"]) == ["BTC", "ETH", "xyz:TSLA"]
    assert route.call_count == 1
    assert bodies(route) == [{"type": "allMids", "dex": "ALL_DEXS"}]


@respx.mock
async def test_api_failure_passes_name_through(client: HydromancerClient) -> None:
    respx.post(API).mock(return_value=httpx.Response(500, text="boom"))
    assert await CoinResolver(client).resolve(" eth ") == "eth"
