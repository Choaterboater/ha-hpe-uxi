"""One event entity per UXI workspace: sensor offline / online, issue confirmed / resolved. Use it as an automation trigger."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeUxiConfigEntry
from .const import EVENT_TYPES
from .coordinator import HpeUxiCoordinator, event_signal
from .entity import UxiEntity, account_device_info, account_uid


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeUxiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([WorkspaceEvents(entry.runtime_data, entry)])


class WorkspaceEvents(UxiEntity, EventEntity):
    _attr_event_types = EVENT_TYPES
    _attr_translation_key = "events"

    def __init__(self, c: HpeUxiCoordinator, entry) -> None:
        super().__init__(c)
        self._entry_id = entry.entry_id
        self._attr_unique_id = f"{account_uid(entry)}_events"
        self._attr_device_info = account_device_info(entry)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(async_dispatcher_connect(self.hass, event_signal(self._entry_id), self._on_event))

    @callback
    def _on_event(self, event_type: str, data: dict[str, Any]) -> None:
        self._trigger_event(event_type, data)
        self.async_write_ha_state()
