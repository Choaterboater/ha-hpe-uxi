"""Each UXI sensor and agent: online, testing, and whether it has an issue that is not resolved."""

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
from .entity import UxiEntity, add_entities_as_seen, agent_device_info, agent_uid, sensor_device_info, sensor_uid
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
                yield f"{sensor_uid(entry, sid)}_{d.key}", lambda s=sid, d=d: SensorState(c, entry, "sensors", s, d)
        for aid in list(c.data.agents):
            for d in DESCRIPTIONS:
                yield f"{agent_uid(entry, aid)}_{d.key}", lambda a=aid, d=d: SensorState(c, entry, "agents", a, d)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class SensorState(UxiEntity, BinarySensorEntity):
    """kind: "sensors" (hardware) or "agents" (UXI as software); both have the same status."""

    entity_description: UxiBinaryDescription

    def __init__(self, c: HpeUxiCoordinator, entry, kind: str, row_id: str, description: UxiBinaryDescription) -> None:
        super().__init__(c)
        self.entity_description = description
        self._kind = kind
        self._id = row_id
        if kind == "agents":
            self._attr_unique_id = f"{agent_uid(entry, row_id)}_{description.key}"
            self._attr_device_info = agent_device_info(c.hass, entry, c.data.agents[row_id])
        else:
            self._attr_unique_id = f"{sensor_uid(entry, row_id)}_{description.key}"
            self._attr_device_info = sensor_device_info(c.hass, entry, c.data.sensors[row_id])

    def _row(self) -> Sensor | None:
        return getattr(self.coordinator.data, self._kind).get(self._id)

    @property
    def available(self) -> bool:
        return super().available and self._row() is not None

    @property
    def is_on(self) -> bool | None:
        s = self._row()
        return self.entity_description.is_on_fn(s) if s else None
