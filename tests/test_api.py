"""The UXI client against fake GreenLake / UXI answers: token reuse and refresh, paging, errors."""

import base64
import json

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.hpe_uxi.api import UxiAuthError, UxiBusyError, UxiClient, UxiError, _jwt_claims
from custom_components.hpe_uxi.const import TOKEN_URL

BASE = "https://api.capenetworks.com/networking-uxi/v1alpha1"
SECRET = "test-secret-do-not-log"


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr("custom_components.hpe_uxi.api.MIN_REQUEST_GAP_SECONDS", 0)


def token_body():
    claims = base64.urlsafe_b64encode(json.dumps({"platform_customer_id": "tenant-x"}).encode()).decode().rstrip("=")
    return {"access_token": f"e30.{claims}.sig", "expires_in": 7199}


async def test_token_reused_paging_follows_next(hass, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, json=token_body())
    # The mocker matches the first registered URL whose query is a subset, so the later page goes first.
    aioclient_mock.get(f"{BASE}/sensors?limit=100&next=c2", json={"items": [{"id": "b"}], "count": 1, "next": None})
    aioclient_mock.get(f"{BASE}/sensors?limit=100", json={"items": [{"id": "a"}], "count": 1, "next": "c2"})
    client = UxiClient(async_get_clientsession(hass), "id", SECRET)
    assert await client.sensors() == [{"id": "a"}, {"id": "b"}]
    assert await client.sensors() == [{"id": "a"}, {"id": "b"}]
    assert sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == TOKEN_URL) == 1
    assert client.tenant_id == "tenant-x"
    token_call = next(c for c in aioclient_mock.mock_calls if str(c[1]) == TOKEN_URL)
    assert token_call[2]["grant_type"] == "client_credentials"
    auth = [c[3]["Authorization"] for c in aioclient_mock.mock_calls if "sensors" in str(c[1])]
    assert all(a.startswith("Bearer e30.") for a in auth)


async def test_401_refreshes_token_once_then_fails(hass, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, json=token_body())
    aioclient_mock.get(f"{BASE}/agents?limit=100", status=401)
    client = UxiClient(async_get_clientsession(hass), "id", SECRET)
    with pytest.raises(UxiAuthError) as info:
        await client.agents()
    assert sum(1 for c in aioclient_mock.mock_calls if str(c[1]) == TOKEN_URL) == 2
    assert SECRET not in str(info.value)


async def test_rate_limited_and_not_found(hass, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, json=token_body())
    aioclient_mock.get(f"{BASE}/sensors/s1/status", status=429, headers={"Retry-After": "7"})
    aioclient_mock.get(f"{BASE}/sensors/s2/status", status=404)
    client = UxiClient(async_get_clientsession(hass), "id", SECRET)
    with pytest.raises(UxiBusyError) as info:
        await client.sensor_status("s1")
    assert info.value.retry_after == 7.0
    with pytest.raises(UxiError) as info:
        await client.sensor_status("s2")
    assert info.value.status == 404


async def test_bad_credentials_never_echo_the_body(hass, aioclient_mock):
    aioclient_mock.post(TOKEN_URL, status=400, text=f'{{"error":"invalid_client","client_secret":"{SECRET}"}}')
    client = UxiClient(async_get_clientsession(hass), "id", SECRET)
    with pytest.raises(UxiAuthError) as info:
        await client.sensors()
    assert SECRET not in str(info.value)


def test_jwt_claims_tolerates_opaque_tokens():
    assert _jwt_claims("opaque") == {}
    assert _jwt_claims("a.!!!.c") == {}
