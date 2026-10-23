"""Each UXI sensor: online, testing, and whether it has an issue that is not resolved."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeUxiConfigEntry
from .coordinator import HpeUxiCoordinator, active_issues
from .entity import UxiEntity, add_entities_as_seen, sensor_device_info, sensor_uid
from .source import Sensor


@dataclass(frozen=True, kw_only=True)
class UxiBinaryDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[Sensor], bool | None]


def _has_issue(s: Sensor) -> bool | None:
    return None if s.get("issues") is None else bool(active_issues(s))


DESCRIPTIONS = (
    UxiBinaryDescription(key="online", translation_key="online", device_class=BinarySensorDeviceClass.CONNECTIVITY,
                         is_on_fn=lambda s: s.get("online")),
    UxiBinaryDescription(key="testing", translation_key="testing", device_class=BinarySensorDeviceClass.RUNNING,
                         is_on_fn=lambda s: s.get("testing")),
    UxiBinaryDescription(key="issue", translation_key="issue", device_class=BinarySensorDeviceClass.PROBLEM,
                         is_on_fn=_has_issue),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeUxiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data

    def candidates():
        for sid in list(c.data.sensors):
            for d in DESCRIPTIONS:
                yield f"{sensor_uid(entry, sid)}_{d.key}", lambda s=sid, d=d: SensorState(c, entry, s, d)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class SensorState(UxiEntity, BinarySensorEntity):
    entity_description: UxiBinaryDescription

    def __init__(self, c: HpeUxiCoordinator, entry, sensor_id: str, description: UxiBinaryDescription) -> None:
        super().__init__(c)
        self.entity_description = description
        self._sid = sensor_id
        self._attr_unique_id = f"{sensor_uid(entry, sensor_id)}_{description.key}"
        self._attr_device_info = sensor_device_info(c.hass, entry, c.data.sensors[sensor_id])

    @property
    def available(self) -> bool:
        return super().available and self._sid in self.coordinator.data.sensors

    @property
    def is_on(self) -> bool | None:
        s = self.coordinator.data.sensors.get(self._sid)
        return self.entity_description.is_on_fn(s) if s else None
