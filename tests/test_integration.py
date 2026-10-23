"""Config flow, options, sweeps, entities, events and outages, with Home Assistant's test helpers and a fake UXI.

Sample data, not from a real network.
"""

import asyncio
import base64
import json
from datetime import timedelta

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.hpe_uxi.const import DOMAIN, INVENTORY_SECONDS, POLL_SECONDS, TOKEN_URL

BASE = "https://api.capenetworks.com/networking-uxi/v1alpha1"
TENANT = "0000000000000000000000000000bbbb"
CLIENT_ID = "test-client-id"
SECRET = "test-secret-do-not-log"
LOBBY = "00000000-0000-0000-0000-000000000001"
OFFICE = "00000000-0000-0000-0000-000000000002"
ISSUE_ID = "00000000-0000-0000-0000-0000000000a1"


def jwt(claims):
    part = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"e30.{part}.sig"


def sensor_row(sid, name, serial, wifi):
    return {"id": sid, "serial": serial, "name": name, "modelNumber": "UX-F5C", "groupName": "Home",
            "groupPath": "000000000001.000000000002", "wifiMacAddress": wifi, "ethernetMacAddress": None,
            "addressNote": None, "latitude": None, "longitude": None, "notes": None, "pcapMode": "off",
            "type": "networking-uxi/sensor"}


SENSORS = [sensor_row(LOBBY, "Lobby", "SAMPLE0001", "02:00:00:00:01:01"),
           sensor_row(OFFICE, "Office", "SAMPLE0002", "020000000202")]
AGENTS = [{"id": "00000000-0000-0000-0000-0000000000b1", "serial": "SAMPLEA1", "name": "Laptop agent",
           "modelNumber": "Example laptop", "groupName": "Home", "groupPath": "000000000001", "wifiMacAddress": None,
           "ethernetMacAddress": None, "notes": None, "pcapMode": "light", "type": "networking-uxi/agent"}]
TESTS = [{"id": "t1", "category": "external", "name": "Example web", "target": "https://www.example.com",
          "template": "http-get", "isEnabled": True, "type": "networking-uxi/service-test"},
         {"id": "t2", "category": "internal", "name": "Printer ping", "target": "192.0.2.20",
          "template": "ping", "isEnabled": False, "type": "networking-uxi/service-test"}]
WIRELESS = [{"id": "w1", "name": "Example", "ssid": "Example", "security": "WPA2_PERSONAL", "ipVersion": "IPV4",
             "hidden": False, "type": "networking-uxi/wireless-network"}]

OK = {"isOnline": True, "isTesting": True, "issues": []}
ISSUE = {"id": ISSUE_ID, "code": "DNS_LOOKUP_FAILED", "severity": "ERROR", "status": "CONFIRMED",
         "timestamp": "2026-10-23T10:00:00Z", "incidentId": None,
         "context": {"customerId": "00000000-0000-0000-0000-0000000000c1", "sensorId": OFFICE, "sensorName": "Office",
                     "networkName": "Example", "serviceTestName": "Example web", "interfaceType": "wlan0",
                     "groupName": "Home"}}
WITH_ISSUE = {"isOnline": True, "isTesting": True, "issues": [ISSUE]}


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    monkeypatch.setattr("custom_components.hpe_uxi.api.MIN_REQUEST_GAP_SECONDS", 0)


def uxi(aioclient_mock, sensors=None, status=None, token_status=200, list_status=200):
    """A fake GreenLake + UXI. status: sensor id -> status body, or an HTTP status code."""
    aioclient_mock.clear_requests()
    aioclient_mock.post(TOKEN_URL, status=token_status,
                        json={"access_token": jwt({"platform_customer_id": TENANT}), "expires_in": 7199})
    rows = sensors if sensors is not None else SENSORS
    aioclient_mock.get(f"{BASE}/sensors?limit=100", status=list_status, json={"items": rows, "count": len(rows), "next": None})
    for path, items in (("agents", AGENTS), ("service-tests", TESTS), ("wireless-networks", WIRELESS)):
        aioclient_mock.get(f"{BASE}/{path}?limit=100", json={"items": items, "count": len(items), "next": None})
    aioclient_mock.get(f"{BASE}/wired-networks?limit=100", status=404)
    for sid, body in {AGENTS[0]["id"]: OK, **(status if status is not None else {LOBBY: OK, OFFICE: OK})}.items():
        if isinstance(body, int):
            aioclient_mock.get(f"{BASE}/sensors/{sid}/status", status=body)
        else:
            aioclient_mock.get(f"{BASE}/sensors/{sid}/status", json=body)


async def settle(hass):
    for _ in range(3):
        await hass.async_block_till_done()
        pending = [t for t in hass._background_tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=5)
    await hass.async_block_till_done()


async def tick(hass, freezer, seconds=POLL_SECONDS + 1):
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await settle(hass)


def entry(options=None):
    return MockConfigEntry(
        domain=DOMAIN,
        title="HPE UXI",
        unique_id=TENANT,
        data={"client_id": CLIENT_ID, "client_secret": SECRET, "base_url": "https://api.capenetworks.com",
              "tenant_id": TENANT},
        options=options if options is not None else {"scan_seconds": POLL_SECONDS},
    )


async def setup(hass, e=None):
    e = e or entry()
    e.add_to_hass(hass)
    assert await hass.config_entries.async_setup(e.entry_id)
    await settle(hass)
    return e


# --- flows ------------------------------------------------------------------------


async def test_config_flow_creates_one_entry_per_workspace(hass, aioclient_mock):
    uxi(aioclient_mock)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert r["step_id"] == "user"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"client_id": f" {CLIENT_ID} ", "client_secret": SECRET})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["title"] == "HPE UXI"
    assert r["data"] == {"client_id": CLIENT_ID, "client_secret": SECRET, "base_url": "https://api.capenetworks.com",
                         "tenant_id": TENANT}
    assert r["result"].unique_id == TENANT
    assert r["result"].options == {"scan_seconds": POLL_SECONDS}
    await settle(hass)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"client_id": CLIENT_ID, "client_secret": SECRET})
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_config_flow_errors(hass, aioclient_mock):
    uxi(aioclient_mock, token_status=401)
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"client_id": "x", "client_secret": "y"})
    assert r["errors"] == {"base": "invalid_auth"}
    uxi(aioclient_mock, list_status=500)
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {"client_id": "x", "client_secret": "y"})
    assert r["errors"] == {"base": "cannot_connect"}


async def test_options_set_how_often(hass, aioclient_mock):
    uxi(aioclient_mock)
    e = await setup(hass)
    r = await hass.config_entries.options.async_init(e.entry_id)
    r = await hass.config_entries.options.async_configure(r["flow_id"], {"scan_seconds": 120})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await settle(hass)
    assert e.options == {"scan_seconds": 120}
    assert e.runtime_data.update_interval == timedelta(seconds=120)


# --- entities -----------------------------------------------------------------------


async def test_workspace_and_sensor_entities(hass, aioclient_mock):
    uxi(aioclient_mock, status={LOBBY: OK, OFFICE: WITH_ISSUE})
    await setup(hass)
    s = hass.states.get
    assert s("sensor.hpe_uxi_sensors_online").state == "2"
    assert s("sensor.hpe_uxi_sensors_offline").state == "0"
    issues = s("sensor.hpe_uxi_active_issues")
    assert issues.state == "1"
    assert issues.attributes["issues"] == [{
        "sensor": "Office", "code": "DNS_LOOKUP_FAILED", "severity": "ERROR", "network": "Example",
        "service_test": "Example web", "interface": "wlan0", "timestamp": "2026-10-23T10:00:00Z"}]
    assert s("sensor.hpe_uxi_agents").state == "1"
    tests = s("sensor.hpe_uxi_service_tests_enabled")
    assert tests.state == "1" and tests.attributes["total"] == 2
    assert s("sensor.hpe_uxi_networks").state == "1"  # the wired list is not there (404): skipped
    assert s("binary_sensor.lobby_online").state == STATE_ON
    assert s("binary_sensor.lobby_testing").state == STATE_ON
    assert s("binary_sensor.lobby_issue").state == STATE_OFF
    assert s("binary_sensor.office_issue").state == STATE_ON
    assert s("sensor.office_issues").state == "1"
    assert s("sensor.office_group").state == "Home"
    dev_reg = dr.async_get(hass)
    e = hass.config_entries.async_entries(DOMAIN)[0]
    account = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_account"), e.entry_id)
    office = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_sensor_{OFFICE}"), e.entry_id)
    group = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_group_000000000001.000000000002"), e.entry_id)
    assert office.via_device_id == group.id and group.via_device_id == account.id
    assert office.serial_number == "SAMPLE0002" and office.model == "UX-F5C"
    assert (dr.CONNECTION_NETWORK_MAC, "02:00:00:00:02:02") in office.connections


async def test_agents_are_devices(hass, aioclient_mock):
    """UXI agents (the "virtual sensors" on laptops, phones and VMs) get a device each, under the workspace."""
    uxi(aioclient_mock)
    e = await setup(hass)
    dev_reg = dr.async_get(hass)
    agent = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_agent_{AGENTS[0]['id']}"), e.entry_id)
    group = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_group_000000000001"), e.entry_id)
    assert agent is not None and agent.name == "Laptop agent"
    assert agent.model == "Example laptop" and agent.serial_number == "SAMPLEA1"
    assert agent.via_device_id == group.id
    assert hass.states.get("sensor.laptop_agent_group").state == "Home"
    # UXI gives an agent's status with the sensor status call, so agents have the same entities.
    assert hass.states.get("binary_sensor.laptop_agent_online").state == STATE_ON
    assert hass.states.get("binary_sensor.laptop_agent_testing").state == STATE_ON
    assert hass.states.get("binary_sensor.laptop_agent_issue").state == STATE_OFF
    assert hass.states.get("sensor.laptop_agent_issues").state == "0"


async def test_agent_status_issues_and_events(hass, aioclient_mock, freezer):
    agent_id = AGENTS[0]["id"]
    uxi(aioclient_mock)
    await setup(hass)
    fired = []

    @callback
    def record(event):
        if event.data["entity_id"] == "event.hpe_uxi_events" and (new := event.data["new_state"]) is not None:
            fired.append(dict(new.attributes))

    hass.bus.async_listen("state_changed", record)
    uxi(aioclient_mock, status={LOBBY: OK, OFFICE: OK, agent_id: {**WITH_ISSUE, "isOnline": False}})
    await tick(hass, freezer)
    assert hass.states.get("binary_sensor.laptop_agent_online").state == STATE_OFF
    assert hass.states.get("binary_sensor.laptop_agent_issue").state == STATE_ON
    assert hass.states.get("sensor.hpe_uxi_active_issues").state == "1"  # the workspace counts agent issues
    assert hass.states.get("sensor.home_active_issues").state == "1"  # and so does the agent's group
    assert [(f["event_type"], f["sensor"]) for f in fired] == [
        ("sensor_offline", "Laptop agent"), ("issue_confirmed", "Laptop agent")]


async def test_groups_are_devices_with_their_own_counts(hass, aioclient_mock):
    """Each UXI group (often a site) is a device under the workspace, with the sensors and agents in it under it."""
    north = sensor_row("00000000-0000-0000-0000-000000000003", "North", "SAMPLE0003", None)
    north |= {"groupName": "Branch North", "groupPath": "000000000001.000000000009"}
    uxi(aioclient_mock, sensors=[*SENSORS, north],
        status={LOBBY: OK, OFFICE: WITH_ISSUE, north["id"]: {**OK, "isOnline": False}})
    e = await setup(hass)
    dev_reg = dr.async_get(hass)
    account = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_account"), e.entry_id)
    branch = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_group_000000000001.000000000009"), e.entry_id)
    assert branch.name == "Branch North" and branch.model == "UXI group" and branch.via_device_id == account.id
    sensor = dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_sensor_{north['id']}"), e.entry_id)
    assert sensor.via_device_id == branch.id
    s = hass.states.get
    assert s("sensor.branch_north_sensors_offline").state == "1"
    assert s("sensor.branch_north_sensors_online").state == "0"
    assert s("sensor.branch_north_active_issues").state == "0"
    # Two groups are named Home: the one with the shorter path keeps the name, the other becomes "Home 2".
    assert s("sensor.home_agents").state == "1"
    assert s("sensor.home_2_sensors_online").state == "2"  # Lobby and Office
    assert s("sensor.home_2_active_issues").state == "1"
    assert s("sensor.hpe_uxi_sensors_offline").state == "1"  # the workspace still counts them all


async def test_agent_list_refused_is_warned_once(hass, aioclient_mock, freezer, caplog):
    uxi(aioclient_mock)
    aioclient_mock._mocks = [m for m in aioclient_mock._mocks if "/agents" not in str(m._url)]
    aioclient_mock.get(f"{BASE}/agents?limit=100", status=403)
    e = await setup(hass)
    assert hass.states.get("sensor.hpe_uxi_agents").state == "0"
    assert e.runtime_data.data.skipped == ["agents", "wired networks"]  # the fake UXI has no wired list (404)
    await tick(hass, freezer, INVENTORY_SECONDS + 1)
    assert caplog.text.count("UXI agents not read") == 1


async def test_diagnostics_redact_credentials_and_identifiers(hass, aioclient_mock):
    from custom_components.hpe_uxi.diagnostics import async_get_config_entry_diagnostics

    uxi(aioclient_mock, status={LOBBY: OK, OFFICE: WITH_ISSUE})
    e = await setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, e)
    assert diag["counts"] == {"sensors": 2, "agents": 1, "service_tests": 2, "networks": 1, "groups": 2}
    assert diag["agents"][0]["name"] == "Laptop agent" and diag["agents"][0]["serial"] == "**REDACTED**"
    text = str(diag)
    for private in (SECRET, CLIENT_ID, TENANT, "SAMPLE0001", "02:00:00:00:01:01"):
        assert private not in text


async def test_events_for_sensors_and_issues(hass, aioclient_mock, freezer):
    uxi(aioclient_mock, status={LOBBY: OK, OFFICE: WITH_ISSUE})
    await setup(hass)
    fired = []

    @callback
    def record(event):
        if event.data["entity_id"] == "event.hpe_uxi_events" and (new := event.data["new_state"]) is not None:
            fired.append(dict(new.attributes))

    hass.bus.async_listen("state_changed", record)
    uxi(aioclient_mock, status={LOBBY: {**OK, "isOnline": False}, OFFICE: {**OK, "issues": [{**ISSUE, "status": "RESOLVED"}]}})
    await tick(hass, freezer)
    assert [f["event_type"] for f in fired] == ["sensor_offline", "issue_resolved"]
    assert fired[0]["sensor"] == "Lobby" and fired[0]["serial"] == "SAMPLE0001"
    assert fired[1]["issue_id"] == ISSUE_ID and fired[1]["code"] == "DNS_LOOKUP_FAILED"
    assert hass.states.get("binary_sensor.lobby_online").state == STATE_OFF
    assert hass.states.get("binary_sensor.office_issue").state == STATE_OFF
    fired.clear()
    uxi(aioclient_mock, status={LOBBY: OK, OFFICE: WITH_ISSUE})
    await tick(hass, freezer)
    assert [f["event_type"] for f in fired] == ["sensor_online", "issue_confirmed"]


# --- trouble ------------------------------------------------------------------------


async def test_outage_keeps_last_state_then_unavailable_and_secret_never_logged(hass, aioclient_mock, freezer, caplog):
    uxi(aioclient_mock)
    await setup(hass)
    uxi(aioclient_mock, status={LOBBY: 503, OFFICE: 503})
    await tick(hass, freezer)
    assert hass.states.get("binary_sensor.lobby_online").state == STATE_ON  # kept through short trouble
    assert "keeping last state" in caplog.text
    await tick(hass, freezer, 400)
    assert hass.states.get("binary_sensor.lobby_online").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.hpe_uxi_sensors_online").state == STATE_UNAVAILABLE
    uxi(aioclient_mock)
    await tick(hass, freezer, 301)
    assert hass.states.get("binary_sensor.lobby_online").state == STATE_ON
    assert SECRET not in caplog.text


async def test_status_refused_for_one_sensor_keeps_its_last_status(hass, aioclient_mock, freezer):
    uxi(aioclient_mock)
    await setup(hass)
    uxi(aioclient_mock, status={LOBBY: OK, OFFICE: 403})
    await tick(hass, freezer)
    assert hass.states.get("binary_sensor.office_online").state == STATE_ON
    assert hass.states.get("binary_sensor.lobby_online").state == STATE_ON


async def test_new_sensor_added_and_removed_sensor_dropped(hass, aioclient_mock, freezer):
    uxi(aioclient_mock, sensors=SENSORS[:1], status={LOBBY: OK})
    e = await setup(hass)
    assert hass.states.get("binary_sensor.office_online") is None
    uxi(aioclient_mock)
    await tick(hass, freezer, INVENTORY_SECONDS + 1)
    assert hass.states.get("binary_sensor.office_online").state == STATE_ON
    uxi(aioclient_mock, sensors=SENSORS[:1], status={LOBBY: OK})
    assert await hass.config_entries.async_reload(e.entry_id)
    await settle(hass)
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_sensor_{OFFICE}"), e.entry_id) is None
    assert dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_sensor_{LOBBY}"), e.entry_id) is not None
    assert dev_reg.async_get_device_by_identifier((DOMAIN, f"{TENANT}_agent_{AGENTS[0]['id']}"), e.entry_id) is not None


async def test_refused_credentials_retry_setup_and_unload(hass, aioclient_mock):
    uxi(aioclient_mock, token_status=401)
    e = entry()
    e.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(e.entry_id)
    assert e.state is ConfigEntryState.SETUP_RETRY
    uxi(aioclient_mock)
    e2 = await setup(hass, MockConfigEntry(domain=DOMAIN, title="HPE UXI 2", unique_id="other",
                                           data={**e.data, "tenant_id": "other"}, options={"scan_seconds": 60}))
    assert await hass.config_entries.async_unload(e2.entry_id)
    assert e2.state is ConfigEntryState.NOT_LOADED
