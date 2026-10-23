"""HPE UXI (unofficial): UXI sensors with their status and issues, agents, service tests and networks."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import UxiClient
from .const import CONF_BASE_URL, CONF_CLIENT_ID, CONF_CLIENT_SECRET, DEFAULT_BASE_URL, DOMAIN
from .coordinator import HpeUxiCoordinator
from .entity import account_device_info, agent_uid, sensor_uid
from .source import UxiSource

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.SENSOR,
]

type HpeUxiConfigEntry = ConfigEntry[HpeUxiCoordinator]


def client_for(hass: HomeAssistant, data: dict[str, Any]) -> UxiClient:
    return UxiClient(async_get_clientsession(hass), data[CONF_CLIENT_ID], data[CONF_CLIENT_SECRET],
                     data.get(CONF_BASE_URL) or DEFAULT_BASE_URL)


async def async_setup_entry(hass: HomeAssistant, entry: HpeUxiConfigEntry) -> bool:
    coordinator = HpeUxiCoordinator(hass, entry, UxiSource(client_for(hass, entry.data)))
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # The workspace is registered first so the UXI sensors can sit under it.
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **account_device_info(entry))
    _remove_stale_devices(hass, entry, coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HpeUxiConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _options_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _remove_stale_devices(hass: HomeAssistant, entry: ConfigEntry, coordinator: HpeUxiCoordinator) -> None:
    """Drop UXI sensors and agents the workspace no longer lists."""
    keep = {sensor_uid(entry, sid) for sid in coordinator.data.sensors} | {
        agent_uid(entry, aid) for aid in coordinator.data.agents}
    prefix = (sensor_uid(entry, ""), agent_uid(entry, ""))
    dev_reg = dr.async_get(hass)
    for dev in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        idents = {i for d, i in dev.identifiers if d == DOMAIN}
        if any(i.startswith(prefix) for i in idents) and not keep & idents:
            dev_reg.async_remove_device(dev.id)
