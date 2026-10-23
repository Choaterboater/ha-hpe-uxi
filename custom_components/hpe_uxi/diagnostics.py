"""Diagnostics download: what UXI listed, with credentials, serials, MACs and notes removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import HpeUxiConfigEntry
from .source import group_names

TO_REDACT = {"client_id", "client_secret", "tenant_id", "serial", "wifi_mac", "ethernet_mac", "notes", "address_note"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: HpeUxiConfigEntry) -> dict[str, Any]:
    d = entry.runtime_data.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "lists_not_read": d.skipped,
        "counts": {"sensors": len(d.sensors), "agents": len(d.agents), "service_tests": len(d.tests),
                   "networks": len(d.networks), "groups": len(group_names(d))},
        "groups": sorted(group_names(d).values()),
        "sensors": [async_redact_data({k: v for k, v in s.items() if k != "issues"}, TO_REDACT)
                    | {"issues": len(s["issues"]) if s.get("issues") is not None else None}
                    for s in d.sensors.values()],
        "agents": [async_redact_data({k: v for k, v in a.items() if k != "issues"}, TO_REDACT)
                   | {"issues": len(a["issues"]) if a.get("issues") is not None else None}
                   for a in d.agents.values()],
    }
