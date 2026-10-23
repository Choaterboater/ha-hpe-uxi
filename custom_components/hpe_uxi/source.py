"""Where UXI sensors, their status, agents, service tests and networks come from.

Everything is normalised here into the plain dicts below, so the rest of the
code never sees the UXI API.

Sensor:  id, name, serial, model, group, group_path, wifi_mac, ethernet_mac, notes, address_note,
         online (bool), testing (bool), issues [Issue] (online / testing / issues are
         None until the sensor's status has been read once).
Issue:   id, code, severity ("ERROR" | "WARNING" | "INFO"), status ("CONFIRMED" |
         "RESOLVED"), timestamp, incident_id, sensor_id, sensor, network_id, network,
         service_test_id, service_test, interface, group.
Agent:   id, name, serial, model, group, group_path, wifi_mac, ethernet_mac, online, testing, issues
         (as for a sensor: UXI reads an agent's status with /sensors/{id}/status too).
A UXI group (often one per site) is known by its path; group_names() names one device per group.
Test:    id, name, category, target, template, enabled (bool).
Network: id, name, kind ("wireless" | "wired"), ssid, security, vlan.
Any value may be None when UXI does not report it. Sensors, agents, tests and
networks are read at most every INVENTORY_SECONDS and cached between; each
sensor's status is read every sweep. A failed status call keeps the last one.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from .api import UxiBusyError, UxiClient, UxiError
from .const import INVENTORY_SECONDS

_LOGGER = logging.getLogger(__name__)

Sensor = dict[str, Any]
Issue = dict[str, Any]
Agent = dict[str, Any]
Test = dict[str, Any]
Network = dict[str, Any]

UNKNOWN_STATUS: dict[str, Any] = {"online": None, "testing": None, "issues": None}


@dataclass
class Snapshot:
    """One full sweep."""

    sensors: dict[str, Sensor] = field(default_factory=dict)  # id -> sensor
    agents: dict[str, Agent] = field(default_factory=dict)
    tests: dict[str, Test] = field(default_factory=dict)
    networks: dict[str, Network] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)  # lists UXI refused (403) or does not have (404)


def mac_pretty(value: Any) -> str | None:
    """"0200000001AB" or "02-00-00-00-01-ab" -> "02:00:00:00:01:ab"; anything else -> None."""
    if not isinstance(value, str):
        return None
    hexdigits = re.sub(r"[^0-9a-f]", "", value.lower())
    return ":".join(hexdigits[i:i + 2] for i in range(0, 12, 2)) if len(hexdigits) == 12 else None


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _upper(value: Any) -> str | None:
    return value.strip().upper() or None if isinstance(value, str) else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def sensor_from(row: dict[str, Any]) -> Sensor:
    return {
        "id": str(row["id"]),
        "name": _text(row.get("name")) or _text(row.get("serial")) or str(row["id"]),
        "serial": _text(row.get("serial")),
        "model": _text(row.get("modelNumber")),
        "group": _text(row.get("groupName")),
        "wifi_mac": mac_pretty(row.get("wifiMacAddress")),
        "ethernet_mac": mac_pretty(row.get("ethernetMacAddress")),
        "group_path": _text(row.get("groupPath")),
        "notes": _text(row.get("notes")),
        "address_note": _text(row.get("addressNote")),
    }


def agent_from(row: dict[str, Any]) -> Agent:
    return {
        "id": str(row["id"]),
        "name": _text(row.get("name")) or _text(row.get("serial")) or str(row["id"]),
        "serial": _text(row.get("serial")),
        "model": _text(row.get("modelNumber")),
        "group": _text(row.get("groupName")),
        "wifi_mac": mac_pretty(row.get("wifiMacAddress")),
        "ethernet_mac": mac_pretty(row.get("ethernetMacAddress")),
        "group_path": _text(row.get("groupPath")),
    }


def service_test_from(row: dict[str, Any]) -> Test:
    return {
        "id": str(row["id"]),
        "name": _text(row.get("name")) or str(row["id"]),
        "category": _text(row.get("category")),
        "target": _text(row.get("target")),
        "template": _text(row.get("template")),
        "enabled": bool(row.get("isEnabled")),
    }


def network_from(row: dict[str, Any], kind: str) -> Network:
    vlan = row.get("vLanId")
    return {
        "id": str(row["id"]),
        "name": _text(row.get("name")) or _text(row.get("ssid")) or str(row["id"]),
        "kind": kind,
        "ssid": _text(row.get("ssid")),
        "security": _text(row.get("security")),
        "vlan": vlan if isinstance(vlan, int) and not isinstance(vlan, bool) else None,
    }


def issue_from(row: dict[str, Any]) -> Issue:
    ctx = row.get("context") if isinstance(row.get("context"), dict) else {}
    return {
        "id": str(row.get("id") or ""),
        "code": _text(row.get("code")),
        "severity": _upper(row.get("severity")),
        "status": _upper(row.get("status")),
        "timestamp": _text(row.get("timestamp")),
        "incident_id": _text(row.get("incidentId")),
        "sensor_id": _text(ctx.get("sensorId")),
        "sensor": _text(ctx.get("sensorName")),
        "network_id": _text(ctx.get("networkId")),
        "network": _text(ctx.get("networkName")),
        "service_test_id": _text(ctx.get("serviceTestId")),
        "service_test": _text(ctx.get("serviceTestName")),
        "interface": _text(ctx.get("interfaceType")),
        "group": _text(ctx.get("groupName")),
    }


def status_from(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "online": _bool(row.get("isOnline")),
        "testing": _bool(row.get("isTesting")),
        "issues": [issue_from(i) for i in row.get("issues") or [] if isinstance(i, dict)],
    }


def group_names(snap: Snapshot) -> dict[str, str]:
    """Group path -> a name for its device: the group's name, with " 2", " 3"... when two groups share one."""
    found: dict[str, str] = {}
    for row in [*snap.sensors.values(), *snap.agents.values()]:
        if path := row.get("group_path"):
            found.setdefault(path, row.get("group") or path)
    out: dict[str, str] = {}
    seen: dict[str, int] = {}
    for path in sorted(found):
        name = found[path]
        seen[name] = seen.get(name, 0) + 1
        out[path] = name if seen[name] == 1 else f"{name} {seen[name]}"
    return out


def _by_id(rows: list[dict[str, Any]], make: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r["id"]): make(r) for r in rows if r.get("id")}


class UxiSource:
    """Sweeps a UXI workspace."""

    def __init__(self, client: UxiClient) -> None:
        self.client = client
        self._inventory: Snapshot | None = None
        self._inventory_at = 0.0
        self._status: dict[str, dict[str, Any]] = {}  # sensor id -> last status read
        self._skipped: set[str] = set()

    async def sweep(self) -> Snapshot:
        """Everything now. Raises SourceError (or a subclass) when the sensor list cannot be read."""
        now = time.time()
        if self._inventory is None or now - self._inventory_at >= INVENTORY_SECONDS:
            self._inventory = Snapshot(
                sensors=_by_id(await self.client.sensors(), sensor_from),
                agents=_by_id(await self._optional("agents", self.client.agents), agent_from),
                tests=_by_id(await self._optional("service tests", self.client.service_tests), service_test_from),
                networks={
                    **_by_id(await self._optional("wireless networks", self.client.wireless_networks),
                             lambda r: network_from(r, "wireless")),
                    **_by_id(await self._optional("wired networks", self.client.wired_networks),
                             lambda r: network_from(r, "wired")),
                },
                skipped=[],
            )
            self._inventory_at = now
        inv = self._inventory
        sensors = {sid: {**s, **await self._sensor_status(sid)} for sid, s in inv.sensors.items()}
        agents = {aid: {**a, **await self._sensor_status(aid)} for aid, a in inv.agents.items()}
        self._status = {sid: self._status[sid] for sid in [*sensors, *agents] if sid in self._status}
        return Snapshot(sensors=sensors, agents=agents, tests=inv.tests, networks=inv.networks,
                        skipped=sorted(self._skipped))

    def forget(self) -> None:
        """Read the sensor list (and the rest of the inventory) again on the next sweep."""
        self._inventory = None

    async def _optional(self, what: str, call: Callable[[], Awaitable[list[dict[str, Any]]]]) -> list[dict[str, Any]]:
        """A list the API client may not be allowed to read (403) or that this UXI does not have (404): empty.

        A refusal is logged as a warning once, because it hides things (agents are often the "virtual sensors").
        """
        try:
            rows = await call()
        except UxiError as err:
            if err.status not in (403, 404):
                raise
            if what not in self._skipped:
                if err.status == 403:
                    _LOGGER.warning("UXI %s not read: UXI refused the API client (%s). Give it read access to %s.",
                                    what, err.status, what)
                else:
                    _LOGGER.debug("UXI %s skipped: %s", what, err)
            self._skipped.add(what)
            return []
        self._skipped.discard(what)
        return rows

    async def _sensor_status(self, sensor_id: str) -> dict[str, Any]:
        """This sensor's status now; when UXI refuses only this sensor (403, 404...) the last one read.

        No answer, 401, 429 and 5xx fail the whole sweep, so an outage is not hidden behind old status.
        """
        try:
            status = status_from(await self.client.sensor_status(sensor_id))
        except UxiBusyError:
            raise
        except UxiError as err:
            if err.status is None or err.status == 401:
                raise
            _LOGGER.debug("UXI status of sensor %s not read: %s", sensor_id, err)
            return self._status.get(sensor_id, UNKNOWN_STATUS)
        self._status[sensor_id] = status
        return status
