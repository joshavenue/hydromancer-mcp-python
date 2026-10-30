import asyncio
import json
from typing import Any

import httpx
import pytest
import respx

from hydromancer_mcp import __version__
from hydromancer_mcp.client import DEFAULT_BASE_URL, HydromancerClient, HydromancerError, resolve_base_url

from .conftest import API, TEST_KEY


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record asyncio.sleep calls instead of waiting."""
    seen: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        seen.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return seen


@respx.mock
async def test_request_shape(client: HydromancerClient) -> None:
    route = respx.post(API).mock(return_value=httpx.Response(200, json={"ok": True}))
    assert await client.info({"type": "clearinghouseState", "user": "0xabc", "dex": None}) == {"ok": True}
    req = route.calls.last.request
    assert req.method == "POST"
    assert str(req.url) == "https://api.hydromancer.xyz/info"
    assert json.loads(req.content) == {"type": "clearinghouseState", "user": "0xabc"}  # None fields dropped
    assert req.headers["Authorization"] == f"Bearer {TEST_KEY}"
    assert req.headers["Content-Type"] == "application/json"
    assert req.headers["Accept"] == "application/json"


@respx.mock
async def test_user_agent_is_always_sent(client: HydromancerClient) -> None:
    route = respx.post(API).mock(return_value=httpx.Response(200, json=[]))
    await client.info({"type": "meta"})
    ua = route.calls.last.request.headers["User-Agent"]
    assert ua.startswith(f"hydromancer-mcp-py/{__version__}")
    assert "python-httpx" not in ua and "urllib" not in ua.lower()


@respx.mock
async def test_key_from_env_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYDROMANCER_API_KEY", f"  {TEST_KEY}\n")
    route = respx.post(API).mock(return_value=httpx.Response(200, json={}))
    await HydromancerClient().info({"type": "meta"})
    assert route.calls.last.request.headers["Authorization"] == f"Bearer {TEST_KEY}"


@respx.mock
async def test_missing_key_never_calls_api() -> None:
    route = respx.post(API)
    with pytest.raises(HydromancerError, match="No Hydromancer API key"):
        await HydromancerClient(api_key="").info({"type": "meta"})
    assert route.call_count == 0


@respx.mock
async def test_empty_and_null_bodies(client: HydromancerClient) -> None:
    respx.post(API).mock(side_effect=[httpx.Response(200, text=""), httpx.Response(200, text="null")])
    assert await client.info({"type": "a"}) is None
    assert await client.info({"type": "b"}) is None


@respx.mock
async def test_non_json_response(client: HydromancerClient) -> None:
    respx.post(API).mock(return_value=httpx.Response(200, content=b"\x28\xb5\x2f\xfd binary"))
    with pytest.raises(HydromancerError, match="non-JSON"):
        await client.info({"type": "perpSnapshots"})


# ---------- 429 handling ----------


@respx.mock
async def test_429_retries_after_retry_after_header(client: HydromancerClient, sleeps: list[float]) -> None:
    route = respx.post(API).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, json={"error": "rate limited", "retryAfterSecs": 9}),
            httpx.Response(200, json={"ok": 1}),
        ]
    )
    assert await client.info({"type": "meta"}) == {"ok": 1}
    assert route.call_count == 2
    assert len(sleeps) == 1 and 2.0 <= sleeps[0] <= 2.25  # header wins over body, plus small jitter


@respx.mock
async def test_429_falls_back_to_body_retry_after_secs(client: HydromancerClient, sleeps: list[float]) -> None:
    respx.post(API).mock(
        side_effect=[httpx.Response(429, json={"error": "rate limited", "retryAfterSecs": 3}), httpx.Response(200, json=[])]
    )
    assert await client.info({"type": "meta"}) == []
    assert 3.0 <= sleeps[0] <= 3.25


@respx.mock
async def test_429_gives_up_after_max_retries(client: HydromancerClient, sleeps: list[float]) -> None:
    route = respx.post(API).mock(return_value=httpx.Response(429, headers={"Retry-After": "1"}, json={"retryAfterSecs": 1}))
    with pytest.raises(HydromancerError) as exc:
        await client.info({"type": "meta"})
    assert exc.value.status == 429
    assert "Rate limit reached" in str(exc.value) and "about 1s" in str(exc.value)
    assert route.call_count == 3  # first try + 2 retries
    assert len(sleeps) == 2


@respx.mock
async def test_429_with_long_wait_is_not_retried(client: HydromancerClient, sleeps: list[float]) -> None:
    route = respx.post(API).mock(return_value=httpx.Response(429, json={"retryAfterSecs": 45}))
    with pytest.raises(HydromancerError, match="about 45s"):
        await client.info({"type": "meta"})
    assert route.call_count == 1
    assert sleeps == []


# ---------- errors and key safety ----------


@pytest.mark.parametrize(
    ("status", "phrase"),
    [(401, "refused the API key"), (403, "refused the API key"), (400, "rejected the request"), (503, "server error"), (418, "HTTP 418")],
)
@respx.mock
async def test_status_messages(client: HydromancerClient, status: int, phrase: str) -> None:
    respx.post(API).mock(return_value=httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(HydromancerError, match=phrase) as exc:
        await client.info({"type": "meta"})
    assert exc.value.status == status


@respx.mock
async def test_key_is_masked_in_errors(client: HydromancerClient) -> None:
    respx.post(API).mock(return_value=httpx.Response(401, text=f"invalid key {TEST_KEY}"))
    with pytest.raises(HydromancerError) as exc:
        await client.info({"type": "meta"})
    assert TEST_KEY not in str(exc.value)
    assert "***" in str(exc.value)


@respx.mock
async def test_network_error_message(client: HydromancerClient) -> None:
    respx.post(API).mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(HydromancerError, match="Could not reach Hydromancer"):
        await client.info({"type": "meta"})


@respx.mock
async def test_timeout_message(client: HydromancerClient) -> None:
    respx.post(API).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(HydromancerError, match="did not answer within"):
        await client.info({"type": "meta"})


@respx.mock
async def test_redirects_are_not_followed(client: HydromancerClient) -> None:
    respx.post(API).mock(return_value=httpx.Response(307, headers={"Location": "https://example.com/steal"}))
    other = respx.post("https://example.com/steal")
    with pytest.raises(HydromancerError, match="HTTP 307"):
        await client.info({"type": "meta"})
    assert other.call_count == 0


@respx.mock
async def test_key_is_masked_even_when_cut_by_truncation(client: HydromancerClient) -> None:
    # Put the key across the 300-character detail boundary.
    body = "x" * (300 - 10) + TEST_KEY
    respx.post(API).mock(return_value=httpx.Response(400, text=body))
    with pytest.raises(HydromancerError) as exc:
        await client.info({"type": "meta"})
    assert TEST_KEY[:10] not in str(exc.value)


@respx.mock
async def test_bearer_tokens_are_masked(client: HydromancerClient) -> None:
    respx.post(API).mock(return_value=httpx.Response(400, text="echo: Authorization: Bearer someOtherToken123"))
    with pytest.raises(HydromancerError) as exc:
        await client.info({"type": "meta"})
    assert "someOtherToken123" not in str(exc.value) and "Bearer ***" in str(exc.value)


@respx.mock
async def test_oversized_response_is_refused() -> None:
    small = HydromancerClient(api_key=TEST_KEY, max_response_bytes=1000)
    respx.post(API).mock(return_value=httpx.Response(200, json={"blob": "x" * 5000}))
    with pytest.raises(HydromancerError, match="larger than"):
        await small.info({"type": "vaultSummaries"})


@respx.mock
async def test_429_jitter_stays_within_ceiling(client: HydromancerClient, sleeps: list[float]) -> None:
    respx.post(API).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "10"}), httpx.Response(200, json={})])
    await client.info({"type": "meta"})
    assert sleeps == [10.0]


@respx.mock
async def test_429_without_hints_backs_off(client: HydromancerClient, sleeps: list[float]) -> None:
    respx.post(API).mock(return_value=httpx.Response(429, text="slow down"))
    with pytest.raises(HydromancerError, match="about 4s"):
        await client.info({"type": "meta"})
    assert [round(s) for s in sleeps] == [1, 2]


@respx.mock
async def test_successful_data_is_not_rewritten(client: HydromancerClient) -> None:
    respx.post(API).mock(return_value=httpx.Response(200, json={"text": "Bearer shares may trade"}))
    assert await client.info({"type": "meta"}) == {"text": "Bearer shares may trade"}


def test_repr_hides_key(client: HydromancerClient) -> None:
    assert TEST_KEY not in repr(client)


# ---------- base URL allowlist ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, DEFAULT_BASE_URL),
        ("", DEFAULT_BASE_URL),
        ("https://api.hydromancer.xyz/info", DEFAULT_BASE_URL),
        ("https://api.hydromancer.xyz", DEFAULT_BASE_URL),
        ("https://api.hydromancer.xyz/info/", DEFAULT_BASE_URL),
        ("https://api-testnet.hydromancer.xyz/info", "https://api-testnet.hydromancer.xyz/info"),
    ],
)
def test_base_url_allowed(raw: Any, expected: str) -> None:
    assert resolve_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "http://api.hydromancer.xyz/info",
        "https://evil.example.com/info",
        "https://api.hydromancer.xyz.evil.com/info",
        "https://user:pw@api.hydromancer.xyz/info",
        "https://api.hydromancer.xyz:8443/info",
        "https://api.hydromancer.xyz/other",
        "https://api.hydromancer.xyz/info?x=1",
        "https://api.hydromancer.xyz:notaport/info",
        "https://api.hydromancer.xyz:99999/info",
    ],
)
def test_base_url_refused(raw: str) -> None:
    with pytest.raises(HydromancerError):
        resolve_base_url(raw)


def test_base_url_env_is_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HYDROMANCER_BASE_URL", "https://evil.example.com/info")
    with pytest.raises(HydromancerError, match="Other hosts are refused"):
        HydromancerClient(api_key=TEST_KEY)
