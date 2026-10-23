"""Shared entity helpers: device info for the UXI workspace, sensors and agents, and adding entities as they appear."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_TENANT_ID, DOMAIN, MANUFACTURER
from .coordinator import HpeUxiCoordinator
from .source import Agent, Sensor, group_names


def tenant_of(entry: ConfigEntry) -> str:
    """The GreenLake workspace id prefixes every unique id."""
    return entry.data[CONF_TENANT_ID]


def account_uid(entry: ConfigEntry) -> str:
    return f"{tenant_of(entry)}_account"


def sensor_uid(entry: ConfigEntry, sensor_id: str) -> str:
    return f"{tenant_of(entry)}_sensor_{sensor_id}"


def agent_uid(entry: ConfigEntry, agent_id: str) -> str:
    return f"{tenant_of(entry)}_agent_{agent_id}"


def group_uid(entry: ConfigEntry, path: str) -> str:
    return f"{tenant_of(entry)}_group_{path}"


def account_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, account_uid(entry))},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model="UXI workspace",
        entry_type=DeviceEntryType.SERVICE,
    )


def group_device_info(entry: ConfigEntry, path: str, name: str) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, group_uid(entry, path))}, name=name, manufacturer=MANUFACTURER,
                      model="UXI group")


@callback
def ensure_group_devices(hass: HomeAssistant, entry: ConfigEntry, coordinator: HpeUxiCoordinator) -> None:
    """UXI groups (often a site each) are registered first, under the workspace, so sensors and agents sit under them."""
    dev_reg = dr.async_get(hass)
    account = dev_reg.async_get_device_by_identifier((DOMAIN, account_uid(entry)), entry.entry_id)
    for path, name in group_names(coordinator.data).items():
        info = group_device_info(entry, path, name)
        if account is not None:
            info["via_device_id"] = account.id
        dev_reg.async_get_or_create(config_entry_id=entry.entry_id, **info)


def sensor_device_info(hass: HomeAssistant, entry: ConfigEntry, sensor: Sensor) -> DeviceInfo:
    """One UXI sensor, under its group (else the workspace)."""
    return _device_info(hass, entry, sensor, sensor_uid(entry, sensor["id"]), "Sensor")


def agent_device_info(hass: HomeAssistant, entry: ConfigEntry, agent: Agent) -> DeviceInfo:
    """One UXI agent (UXI running as software: a laptop, a phone, a VM), under its group (else the workspace)."""
    return _device_info(hass, entry, agent, agent_uid(entry, agent["id"]), "Agent")


def _device_info(hass: HomeAssistant, entry: ConfigEntry, row: Sensor | Agent, uid: str, model: str) -> DeviceInfo:
    info = DeviceInfo(
        identifiers={(DOMAIN, uid)},
        name=row["name"],
        manufacturer=MANUFACTURER,
        model=row.get("model") or model,
    )
    if macs := {m for m in (row.get("wifi_mac"), row.get("ethernet_mac")) if m}:
        info["connections"] = {(dr.CONNECTION_NETWORK_MAC, m) for m in macs}
    if row.get("serial"):
        info["serial_number"] = row["serial"]
    dev_reg = dr.async_get(hass)
    parent = None
    if row.get("group_path"):
        parent = dev_reg.async_get_device_by_identifier((DOMAIN, group_uid(entry, row["group_path"])), entry.entry_id)
    parent = parent or dev_reg.async_get_device_by_identifier((DOMAIN, account_uid(entry)), entry.entry_id)
    if parent is not None:
        info["via_device_id"] = parent.id
    return info


class UxiEntity(CoordinatorEntity[HpeUxiCoordinator]):
    _attr_has_entity_name = True


@callback
def add_entities_as_seen(
    entry: ConfigEntry,
    coordinator: HpeUxiCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    candidates: Callable[[], Iterable[tuple[str, Callable[[], Entity]]]],
) -> None:
    """Add entities now, and later whenever a sweep brings new UXI sensors."""
    added: set[str] = set()

    @callback
    def add_new() -> None:
        new = []
        for uid, make in candidates():
            if uid not in added:
                added.add(uid)
                new.append(make())
        if new:
            async_add_entities(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
