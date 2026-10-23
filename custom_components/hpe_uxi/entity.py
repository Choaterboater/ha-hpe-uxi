"""Shared entity helpers: device info for the UXI workspace and each UXI sensor, and adding entities as sensors appear."""

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
from .source import Sensor


def tenant_of(entry: ConfigEntry) -> str:
    """The GreenLake workspace id prefixes every unique id."""
    return entry.data[CONF_TENANT_ID]


def account_uid(entry: ConfigEntry) -> str:
    return f"{tenant_of(entry)}_account"


def sensor_uid(entry: ConfigEntry, sensor_id: str) -> str:
    return f"{tenant_of(entry)}_sensor_{sensor_id}"


def account_device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, account_uid(entry))},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model="UXI workspace",
        entry_type=DeviceEntryType.SERVICE,
    )


def sensor_device_info(hass: HomeAssistant, entry: ConfigEntry, sensor: Sensor) -> DeviceInfo:
    """One UXI sensor, under the workspace device."""
    info = DeviceInfo(
        identifiers={(DOMAIN, sensor_uid(entry, sensor["id"]))},
        name=sensor["name"],
        manufacturer=MANUFACTURER,
        model=sensor.get("model") or "Sensor",
    )
    if macs := {m for m in (sensor.get("wifi_mac"), sensor.get("ethernet_mac")) if m}:
        info["connections"] = {(dr.CONNECTION_NETWORK_MAC, m) for m in macs}
    if sensor.get("serial"):
        info["serial_number"] = sensor["serial"]
    account = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, account_uid(entry)), entry.entry_id)
    if account is not None:
        info["via_device_id"] = account.id
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
