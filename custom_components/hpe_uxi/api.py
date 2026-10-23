"""Small async client for the HPE Aruba Networking UXI API (no Home Assistant imports, so it is testable alone).

Authentication is the OAuth client-credentials grant against GreenLake (a
personal API client made for the UXI service); the token is cached, refreshed
a minute before it expires and once more on a 401. Lists page with "limit" and
the "next" cursor. Only read calls are made: this integration changes nothing in UXI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import aiohttp

from .const import API_PATH, DEFAULT_BASE_URL, MIN_REQUEST_GAP_SECONDS, PAGE_LIMIT, REQUEST_TIMEOUT_SECONDS, TOKEN_URL
from .errors import SourceAuthError, SourceBusyError, SourceError


class UxiError(SourceError):
    """UXI could not be reached or answered with an error."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class UxiAuthError(UxiError, SourceAuthError):
    """The client id / secret or the token was refused, or the client may not read this."""


class UxiBusyError(UxiError, SourceBusyError):
    """UXI asked us to slow down (429) or had a server error (5xx)."""

    def __init__(self, status: int, retry_after: float | None) -> None:
        super().__init__(f"UXI answered {status}", status)
        self.retry_after = retry_after


def clean_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    return url if url.startswith(("https://", "http://")) else f"https://{url}"


class UxiClient:
    """Read calls to the UXI configuration and status API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        client_id: str,
        client_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        token_url: str = TOKEN_URL,
    ) -> None:
        self._session = session
        self._base = clean_base_url(base_url) + API_PATH
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._token_url = token_url
        self._token: str | None = None
        self._token_exp = 0.0
        self._token_lock = asyncio.Lock()
        self._pace_lock = asyncio.Lock()
        self._last_request = 0.0
        self.claims: dict[str, Any] = {}

    # --- token ---------------------------------------------------------------

    async def token(self, force: bool = False) -> str:
        async with self._token_lock:
            if not force and self._token and time.time() < self._token_exp - 60:
                return self._token
            try:
                async with self._session.post(
                    self._token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    # Never echo the body: it may contain secrets.
                    if resp.status in (400, 401, 403):
                        raise UxiAuthError(f"GreenLake refused the client id / secret ({resp.status})", resp.status)
                    if resp.status == 429 or resp.status >= 500:
                        raise UxiBusyError(resp.status, _retry_after(resp.headers))
                    if resp.status != 200:
                        raise UxiError(f"GreenLake token endpoint answered {resp.status}", resp.status)
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise UxiError(f"Cannot reach GreenLake: {type(err).__name__}") from err
            if not isinstance(body, dict) or not body.get("access_token"):
                raise UxiError("GreenLake token endpoint gave no token")
            self._token = body["access_token"]
            self._token_exp = time.time() + float(body.get("expires_in") or 7200)
            self.claims = _jwt_claims(self._token)
            return self._token

    @property
    def tenant_id(self) -> str | None:
        """The GreenLake workspace the credentials belong to (from the token), if present."""
        return self.claims.get("platform_customer_id") or None

    # --- requests ------------------------------------------------------------

    async def _pace(self) -> None:
        async with self._pace_lock:
            wait = self._last_request + MIN_REQUEST_GAP_SECONDS - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def get(self, path: str, params: dict[str, str] | None = None) -> Any:
        """One GET; retried once with a fresh token on 401. Secrets never appear in errors."""
        for attempt in (0, 1):
            token = await self.token(force=attempt == 1)
            await self._pace()
            try:
                async with self._session.get(
                    self._base + path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
                ) as resp:
                    if resp.status == 401 and attempt == 0:
                        continue
                    if resp.status in (401, 403):
                        raise UxiAuthError(f"UXI refused the request ({resp.status} for {path})", resp.status)
                    if resp.status == 429 or resp.status >= 500:
                        raise UxiBusyError(resp.status, _retry_after(resp.headers))
                    if resp.status >= 400:
                        raise UxiError(f"UXI answered {resp.status} for {path}", resp.status)
                    text = await resp.text()
                    return json.loads(text) if text.strip() else None
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
                raise UxiError(f"Cannot reach UXI: {type(err).__name__}") from err
        raise UxiAuthError("UXI refused the token", 401)

    async def items(self, path: str) -> list[dict[str, Any]]:
        """Every item of a list endpoint, following the "next" cursor."""
        query = {"limit": str(PAGE_LIMIT)}
        out: list[dict[str, Any]] = []
        for _ in range(100):
            page = await self.get(path, query) or {}
            rows = page.get("items") or []
            out.extend(r for r in rows if isinstance(r, dict))
            if not rows or not page.get("next"):
                break
            query["next"] = str(page["next"])
        return out

    # --- endpoints -------------------------------------------------------------

    async def sensors(self) -> list[dict[str, Any]]:
        return await self.items("/sensors")

    async def sensor_status(self, sensor_id: str) -> dict[str, Any]:
        return await self.get(f"/sensors/{sensor_id}/status") or {}

    async def agents(self) -> list[dict[str, Any]]:
        return await self.items("/agents")

    async def service_tests(self) -> list[dict[str, Any]]:
        return await self.items("/service-tests")

    async def wireless_networks(self) -> list[dict[str, Any]]:
        return await self.items("/wireless-networks")

    async def wired_networks(self) -> list[dict[str, Any]]:
        return await self.items("/wired-networks")


def _jwt_claims(token: str) -> dict[str, Any]:
    """Claims of a JWT access token, without verifying it (only used to name the workspace)."""
    try:
        payload = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _retry_after(headers: Any) -> float | None:
    for key in ("Retry-After", "X-RateLimit-Reset"):
        try:
            if value := headers.get(key):
                return max(0.0, float(value))
        except (TypeError, ValueError):
            continue
    return None
