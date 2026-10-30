import json
from typing import Any

import pytest

from hydromancer_mcp._compat import ToolError
from hydromancer_mcp.client import HydromancerClient
from hydromancer_mcp.server import create_server

API = "https://api.hydromancer.xyz/info"
TEST_KEY = "hm_test_key_0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if "live" in request.keywords:
        return
    for name in ("HYDROMANCER_API_KEY", "HYDROMANCER_BASE_URL", "HYDROMANCER_TIMEOUT_MS", "HYDROMANCER_MAX_RESULT_CHARS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def client() -> HydromancerClient:
    return HydromancerClient(api_key=TEST_KEY)


@pytest.fixture
def server(client: HydromancerClient) -> Any:
    return create_server(client)[0]


def _text(result: Any) -> str:
    # mcp 2.x returns a CallToolResult; mcp 1.x returns content blocks or (content, structured).
    content = getattr(result, "content", None)
    if content is None:
        content = result[0] if isinstance(result, tuple) else result
    return "".join(getattr(c, "text", "") for c in content)


async def call(server: Any, name: str, args: dict[str, Any]) -> tuple[bool, str]:
    """Call a tool in-process. Returns (is_error, text)."""
    try:
        result = await server.call_tool(name, args)
    except ToolError as err:
        return True, str(err)
    if getattr(result, "is_error", False) or getattr(result, "isError", False):
        return True, _text(result)
    return False, _text(result)


async def call_json(server: Any, name: str, args: dict[str, Any]) -> Any:
    is_error, text = await call(server, name, args)
    assert not is_error, text
    return json.loads(text)


def bodies(route: Any) -> list[dict[str, Any]]:
    return [json.loads(c.request.content) for c in route.calls]
