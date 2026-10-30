"""Minimal Hydromancer REST client: POST https://api.hydromancer.xyz/info with a Bearer key.

Errors become plain-English messages a model can relay to a non-technical user. The API key
is read from the environment, sent only to the Hydromancer API hosts and never printed.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import __version__

DEFAULT_BASE_URL = "https://api.hydromancer.xyz/info"
ALLOWED_HOSTS = frozenset({"api.hydromancer.xyz", "api-testnet.hydromancer.xyz"})
# Cloudflare in front of the API rejects default library user agents (403, error code 1010).
USER_AGENT = f"hydromancer-mcp-py/{__version__} (+https://docs.hydromancer.xyz)"
KEY_HELP = "https://docs.hydromancer.xyz/readme/get-api-keys"


class HydromancerError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def resolve_base_url(raw: str | None) -> str:
    """Return the /info URL to call. Only HTTPS Hydromancer API hosts are accepted, so the key
    can never be sent anywhere else, whatever HYDROMANCER_BASE_URL says."""
    if not raw or not raw.strip():
        return DEFAULT_BASE_URL
    url = raw.strip()
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS or parts.port not in (None, 443):
        raise HydromancerError(
            "HYDROMANCER_BASE_URL must be https://api.hydromancer.xyz/info or "
            "https://api-testnet.hydromancer.xyz/info. Other hosts are refused so your API key stays private."
        )
    if parts.username or parts.password or parts.query or parts.fragment:
        raise HydromancerError("HYDROMANCER_BASE_URL must not contain credentials, a query or a fragment.")
    path = parts.path.rstrip("/")
    if path in ("", "/"):
        path = "/info"
    if path != "/info":
        raise HydromancerError("HYDROMANCER_BASE_URL must point at the /info endpoint.")
    return f"https://{parts.hostname}/info"


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


class HydromancerClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_s: float | None = None,
        max_retries: int = 2,
        max_retry_wait_s: float = 10.0,
    ) -> None:
        key = api_key if api_key is not None else os.environ.get("HYDROMANCER_API_KEY", "")
        self._api_key = key.strip()
        self.base_url = resolve_base_url(base_url if base_url is not None else os.environ.get("HYDROMANCER_BASE_URL"))
        self.timeout_s = timeout_s if timeout_s is not None else _env_float("HYDROMANCER_TIMEOUT_MS", 30_000) / 1000
        self.max_retries = max_retries
        self.max_retry_wait_s = max_retry_wait_s
        self._http: httpx.AsyncClient | None = None

    def __repr__(self) -> str:  # never show the key
        return f"HydromancerClient(base_url={self.base_url!r}, key={'set' if self.has_key() else 'missing'})"

    def has_key(self) -> bool:
        return bool(self._api_key)

    def mask(self, text: str) -> str:
        """Remove the API key from any text that might be shown to the user or a model."""
        if self._api_key and self._api_key in text:
            text = text.replace(self._api_key, "***")
        return text

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def info(self, body: dict[str, Any]) -> Any:
        if not self.has_key():
            raise HydromancerError(
                "No Hydromancer API key is set. Add HYDROMANCER_API_KEY to this MCP server's env settings "
                f"(get a key at {KEY_HELP}), then restart your AI app."
            )
        # Drop None fields so optional parameters are simply omitted.
        clean = {k: v for k, v in body.items() if v is not None}
        kind = str(clean.get("type", "?"))
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        attempt = 0
        while True:
            try:
                res = await self._client().post(self.base_url, json=clean, headers=headers)
            except httpx.TimeoutException:
                raise HydromancerError(
                    f"Hydromancer did not answer within {self.timeout_s:g}s. Try a shorter time range or a smaller limit."
                ) from None
            except httpx.HTTPError as err:
                raise HydromancerError(
                    f"Could not reach Hydromancer ({self.mask(type(err).__name__)}). Check the internet connection."
                ) from None
            if res.status_code == 429 and attempt < self.max_retries:
                wait = retry_after_seconds(res, attempt)
                if wait <= self.max_retry_wait_s:
                    attempt += 1
                    await asyncio.sleep(wait + random.uniform(0, 0.25))
                    continue
            break

        text = res.text
        if not res.is_success:
            raise HydromancerError(self.mask(explain_status(res, text, kind)), res.status_code)
        stripped = text.strip()
        if stripped in ("", "null"):
            return None
        try:
            return json.loads(stripped)
        except ValueError:
            raise HydromancerError(
                f'Hydromancer returned a non-JSON response for "{kind}". Binary endpoints such as snapshots '
                "are not supported by this server."
            ) from None


def retry_after_seconds(res: httpx.Response, attempt: int) -> float:
    """Seconds to wait after a 429: Retry-After (seconds or HTTP date), then body retryAfterSecs, then backoff."""
    header = res.headers.get("Retry-After")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            try:
                return max(0.0, parsedate_to_datetime(header).timestamp() - time.time())
            except (TypeError, ValueError):
                pass
    try:
        secs = json.loads(res.text).get("retryAfterSecs")
        if secs is not None:
            return max(0.0, float(secs))
    except (ValueError, AttributeError, TypeError):
        pass
    return min(30.0, 2.0**attempt)


def explain_status(res: httpx.Response, text: str, kind: str) -> str:
    status = res.status_code
    detail = re.sub(r"\s+", " ", text).strip()[:300]
    if status in (401, 403):
        return (
            f'Hydromancer refused the API key (HTTP {status}). The key may be wrong, expired, or not allowed to use "{kind}" '
            f"(some endpoints are paid add-ons). Server said: {detail}"
        )
    if status == 429:
        wait = retry_after_seconds(res, 0)
        return (
            f"Rate limit reached (HTTP 429). Wait about {wait:.0f}s and try again, or ask for less data. Server said: {detail}"
        )
    if status in (400, 422):
        return (
            f'Hydromancer rejected the request for "{kind}" (HTTP {status}). Usually a wrong coin name, address or time range. '
            f"Server said: {detail}"
        )
    if status >= 500:
        return f"Hydromancer had a server error (HTTP {status}). Try again shortly. Server said: {detail}"
    return f'Hydromancer returned HTTP {status} for "{kind}". Server said: {detail}'
