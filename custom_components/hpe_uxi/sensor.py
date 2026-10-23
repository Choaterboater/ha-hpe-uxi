"""Sensors for the UXI workspace (sensor and issue counts, agents, service tests, networks) and each UXI sensor."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HpeUxiConfigEntry
from .const import MAX_LIST_ROWS
from .coordinator import HpeUxiCoordinator, active_issues
from .entity import (
    UxiEntity,
    account_device_info,
    account_uid,
    add_entities_as_seen,
    agent_device_info,
    agent_uid,
    sensor_device_info,
    sensor_uid,
)
from .source import Agent, Issue, Sensor, Snapshot

# List-shaped attributes stay out of the recorder.
_LIST_ATTRS = frozenset({"issues", "agents", "tests", "networks"})
_MEASURE = SensorStateClass.MEASUREMENT


@dataclass(frozen=True, kw_only=True)
class UxiSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Any], Any]
    attrs_fn: Callable[[Any], dict[str, Any]] | None = None


def _issue_row(i: Issue) -> dict[str, Any]:
    return {k: i.get(k) for k in ("sensor", "code", "severity", "network", "service_test", "interface", "timestamp")}


def _issue_rows(issues: list[Issue]) -> list[dict[str, Any]]:
    """Newest first."""
    return [_issue_row(i) for i in sorted(issues, key=lambda i: i.get("timestamp") or "", reverse=True)][:MAX_LIST_ROWS]


def _all_issues(d: Snapshot) -> list[Issue]:
    return [i for s in d.sensors.values() for i in active_issues(s)]


def _by_name(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{k: r.get(k) for k in keys} for r in sorted(rows, key=lambda r: str(r["name"]).casefold())][:MAX_LIST_ROWS]


# --- the workspace: value_fn(snapshot) ---------------------------------------

ACCOUNT_SENSORS = (
    UxiSensorDescription(
        key="sensors_online", translation_key="sensors_online", state_class=_MEASURE,
        value_fn=lambda d: sum(1 for s in d.sensors.values() if s.get("online") is True),
    ),
    UxiSensorDescription(
        key="sensors_offline", translation_key="sensors_offline", state_class=_MEASURE,
        value_fn=lambda d: sum(1 for s in d.sensors.values() if s.get("online") is False),
    ),
    UxiSensorDescription(
        key="active_issues", translation_key="active_issues", state_class=_MEASURE,
        value_fn=lambda d: len(_all_issues(d)),
        attrs_fn=lambda d: {"issues": _issue_rows(_all_issues(d))},
    ),
    UxiSensorDescription(
        key="agents", translation_key="agents", state_class=_MEASURE,
        value_fn=lambda d: len(d.agents),
        attrs_fn=lambda d: {"agents": _by_name(list(d.agents.values()), ("name", "serial", "model", "group"))},
    ),
    UxiSensorDescription(
        key="service_tests", translation_key="service_tests", state_class=_MEASURE,
        value_fn=lambda d: sum(1 for t in d.tests.values() if t["enabled"]),
        attrs_fn=lambda d: {"total": len(d.tests),
                            "tests": _by_name(list(d.tests.values()), ("name", "category", "target", "enabled"))},
    ),
    UxiSensorDescription(
        key="networks", translation_key="networks", state_class=_MEASURE,
        value_fn=lambda d: len(d.networks),
        attrs_fn=lambda d: {"networks": _by_name(list(d.networks.values()), ("name", "kind", "ssid", "security", "vlan"))},
    ),
)

# --- each UXI sensor: value_fn(sensor) ----------------------------------------

DEVICE_SENSORS = (
    UxiSensorDescription(
        key="issues", translation_key="issues", state_class=_MEASURE,
        value_fn=lambda s: None if s.get("issues") is None else len(active_issues(s)),
        attrs_fn=lambda s: {"issues": _issue_rows(active_issues(s))},
    ),
    UxiSensorDescription(
        key="group", translation_key="group", entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.get("group"),
    ),
)

# --- each UXI agent: value_fn(agent). UXI's API has no status for agents, only what they are. ----

AGENT_SENSORS = (
    UxiSensorDescription(
        key="group", translation_key="group", entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda a: a.get("group"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HpeUxiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    c = entry.runtime_data
    async_add_entities(AccountSensor(c, entry, d) for d in ACCOUNT_SENSORS)

    def candidates():
        for sid in list(c.data.sensors):
            for d in DEVICE_SENSORS:
                yield f"{sensor_uid(entry, sid)}_{d.key}", lambda s=sid, d=d: DeviceSensor(c, entry, s, d)
        for aid in list(c.data.agents):
            for d in AGENT_SENSORS:
                yield f"{agent_uid(entry, aid)}_{d.key}", lambda a=aid, d=d: AgentSensor(c, entry, a, d)

    add_entities_as_seen(entry, c, async_add_entities, candidates)


class AccountSensor(UxiEntity, SensorEntity):
    entity_description: UxiSensorDescription
    _unrecorded_attributes = _LIST_ATTRS

    def __init__(self, c: HpeUxiCoordinator, entry, description: UxiSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = description
        self._attr_unique_id = f"{account_uid(entry)}_{description.key}"
        self._attr_device_info = account_device_info(entry)

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None


class DeviceSensor(UxiEntity, SensorEntity):
    entity_description: UxiSensorDescription
    _unrecorded_attributes = _LIST_ATTRS

    def __init__(self, c: HpeUxiCoordinator, entry, sensor_id: str, description: UxiSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = description
        self._sid = sensor_id
        self._attr_unique_id = f"{sensor_uid(entry, sensor_id)}_{description.key}"
        self._attr_device_info = sensor_device_info(c.hass, entry, c.data.sensors[sensor_id])

    def _sensor(self) -> Sensor | None:
        return self.coordinator.data.sensors.get(self._sid)

    @property
    def available(self) -> bool:
        return super().available and self._sensor() is not None

    @property
    def native_value(self) -> Any:
        s = self._sensor()
        return self.entity_description.value_fn(s) if s else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn, s = self.entity_description.attrs_fn, self._sensor()
        return fn(s) if fn and s else None


class AgentSensor(UxiEntity, SensorEntity):
    entity_description: UxiSensorDescription

    def __init__(self, c: HpeUxiCoordinator, entry, agent_id: str, description: UxiSensorDescription) -> None:
        super().__init__(c)
        self.entity_description = description
        self._aid = agent_id
        self._attr_unique_id = f"{agent_uid(entry, agent_id)}_{description.key}"
        self._attr_device_info = agent_device_info(c.hass, entry, c.data.agents[agent_id])

    def _agent(self) -> Agent | None:
        return self.coordinator.data.agents.get(self._aid)

    @property
    def available(self) -> bool:
        return super().available and self._agent() is not None

    @property
    def native_value(self) -> Any:
        a = self._agent()
        return self.entity_description.value_fn(a) if a else None
