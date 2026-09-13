"""Diagnostic connectivity sensors for the Flexom integration.

Surfaces, as live dashboard/history/automation-able entities, the exact same
connection state that diagnostics.py already exposes as a one-shot
"Download diagnostics" JSON dump - there was previously no way to see at a
glance (or alert on) "is the realtime channel down right now", only to
notice it after the fact in home-assistant.log.
"""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import Any, Dict, Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .hemis import HemisApiClient
from .websocket import HemisWebSocketClient

_LOGGER = logging.getLogger(__name__)

# Plain polling is enough here: these entities only read attributes already
# held in memory on ws_client/hemis_client, no I/O of their own.
SCAN_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Flexom diagnostic binary sensors from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    ws_client = data["ws_client"]
    hemis_client = data["hemis_client"]
    building_id = data["hemisphere_client"].building_id

    async_add_entities(
        [
            FlexomWebSocketConnectedSensor(building_id, ws_client),
            FlexomApiReachableSensor(building_id, hemis_client),
        ]
    )


class _FlexomDiagnosticSensor(BinarySensorEntity):
    """Shared device info for the two integration-level diagnostic sensors.

    Not tied to any single physical Ubiant device (light/cover/switch), so
    these get their own lightweight "hub" device rather than attaching to
    one of the real actuator devices.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = True

    def __init__(self, building_id: str) -> None:
        self._building_id = building_id

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the shared Flexom integration hub."""
        return {
            "identifiers": {(DOMAIN, f"{self._building_id}-integration")},
            "name": "Flexom",
            "manufacturer": "Ubiant",
            "model": "Hemis Cloud",
        }


class FlexomWebSocketConnectedSensor(_FlexomDiagnosticSensor):
    """Whether the Hemis STOMP WebSocket (realtime channel) is connected."""

    _attr_name = "Flexom WebSocket connected"

    def __init__(self, building_id: str, ws_client: HemisWebSocketClient) -> None:
        super().__init__(building_id)
        self._ws_client = ws_client

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{DOMAIN}_{self._building_id}_websocket_connected"

    @property
    def is_on(self) -> bool:
        """Return True if the WebSocket is currently connected and running."""
        return self._ws_client.ws is not None and self._ws_client.should_run

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return diagnostic attributes matching diagnostics.py's websocket section."""
        last_received = self._ws_client.last_received
        return {
            "listening": self._ws_client.should_run,
            "reconnect_count": self._ws_client.reconnect_count,
            "last_disconnect_reason": self._ws_client.last_disconnect_reason,
            "seconds_since_last_message": (
                round(time.time() - last_received, 1) if last_received else None
            ),
        }


class FlexomApiReachableSensor(_FlexomDiagnosticSensor):
    """Whether the Hemis REST API answered the last periodic health check."""

    _attr_name = "Flexom API reachable"

    def __init__(self, building_id: str, hemis_client: HemisApiClient) -> None:
        super().__init__(building_id)
        self._hemis_client = hemis_client

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{DOMAIN}_{self._building_id}_api_reachable"

    @property
    def is_on(self) -> Optional[bool]:
        """Return True/False once a health check has run, None ("unknown") before that."""
        return self._hemis_client.last_api_call_ok

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        """Return the timestamp of the last health check, if any."""
        last_check = self._hemis_client.last_api_call_time
        return {
            "last_checked_seconds_ago": (
                round(time.time() - last_check, 1) if last_check else None
            ),
        }
