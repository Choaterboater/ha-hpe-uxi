"""Keeps the UXI workspace (sensors with their status and issues, agents, service tests, networks) from REST sweeps."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN,
    DOMAIN,
    EVENT_ISSUE_CONFIRMED,
    EVENT_ISSUE_RESOLVED,
    EVENT_SENSOR_OFFLINE,
    EVENT_SENSOR_ONLINE,
    MAX_BACKOFF_SECONDS,
    POLL_SECONDS,
    UNAVAILABLE_AFTER_SECONDS,
)
from .errors import SourceBusyError, SourceError
from .source import Issue, Sensor, Snapshot, UxiSource

_LOGGER = logging.getLogger(__name__)


def event_signal(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_event"


def active_issues(sensor: Sensor) -> list[Issue]:
    """The sensor's issues that are not resolved (empty while its status is unknown)."""
    return [i for i in sensor.get("issues") or [] if i.get("status") != "RESOLVED"]


class HpeUxiCoordinator(DataUpdateCoordinator[Snapshot]):
    """One coordinator per UXI workspace (config entry): a sweep every scan_seconds."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, source: UxiSource) -> None:
        self.poll_seconds = int(entry.options.get(CONF_SCAN, POLL_SECONDS))
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=timedelta(seconds=self.poll_seconds),
        )
        self.source = source
        self.last_ok: float | None = None
        self._backoff = 0.0

    async def _async_update_data(self) -> Snapshot:
        now = time.time()
        try:
            snap = await self.source.sweep()
        except SourceError as err:
            return self._failed(err, now)
        self._backoff = 0.0
        self._set_interval()
        self.last_ok = now
        if self.data is not None:
            self._events(self.data, snap)
        return snap

    def _set_interval(self) -> None:
        self.update_interval = timedelta(seconds=max(self.poll_seconds, self._backoff))

    def _failed(self, err: SourceError, now: float) -> Snapshot:
        """Keep the last state through short trouble (at least two sweeps); unavailable after that."""
        if isinstance(err, SourceBusyError):
            self._backoff = min(MAX_BACKOFF_SECONDS, max(self._backoff * 2 or self.poll_seconds * 2, err.retry_after or 0))
            self._set_interval()
        keep = max(UNAVAILABLE_AFTER_SECONDS, 2 * self.poll_seconds + 15)
        if self.data is not None and self.last_ok is not None and now - self.last_ok < keep:
            _LOGGER.warning("UXI check failed (%s); keeping last state, next try in %ss",
                            err, int(self.update_interval.total_seconds()) if self.update_interval else self.poll_seconds)
            return self.data
        raise UpdateFailed(str(err)) from err

    # --- events -----------------------------------------------------------

    def _events(self, old: Snapshot, new: Snapshot) -> None:
        """Sensor or agent online / offline and issue confirmed / resolved, compared with the last good sweep."""
        rows = [(sid, s, old.sensors.get(sid)) for sid, s in new.sensors.items()]
        rows += [(aid, a, old.agents.get(aid)) for aid, a in new.agents.items()]
        for sid, s, was in rows:
            if was is None:
                continue  # a new sensor or agent: no event
            if None not in (was.get("online"), s.get("online")) and was["online"] != s["online"]:
                self._emit(EVENT_SENSOR_ONLINE if s["online"] else EVENT_SENSOR_OFFLINE,
                           {"sensor": s["name"], "sensor_id": sid, "serial": s.get("serial")})
            if was.get("issues") is None or s.get("issues") is None:
                continue
            before = {i["id"]: i for i in active_issues(was)}
            after = {i["id"]: i for i in active_issues(s)}
            resolved = {i["id"]: i for i in s["issues"] if i.get("status") == "RESOLVED"}
            for iid, issue in after.items():
                if iid not in before:
                    self._emit(EVENT_ISSUE_CONFIRMED, _issue_event(s, issue))
            for iid, issue in before.items():
                if iid not in after:
                    self._emit(EVENT_ISSUE_RESOLVED, _issue_event(s, resolved.get(iid, issue)))

    @callback
    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        if self.config_entry:
            async_dispatcher_send(self.hass, event_signal(self.config_entry.entry_id), event_type, data)


def _issue_event(sensor: Sensor, issue: Issue) -> dict[str, Any]:
    return {
        "sensor": sensor["name"], "sensor_id": sensor["id"], "issue_id": issue.get("id"),
        **{k: issue.get(k) for k in ("code", "severity", "network", "service_test", "timestamp")},
    }
