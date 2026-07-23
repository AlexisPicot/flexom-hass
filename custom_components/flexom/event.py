"""Support for Flexom physical wall switches.

A wall switch press is momentary, not a persistent on/off state (that's
exactly what triggers BRI/BRIEXT actuator changes elsewhere) - so this uses
Home Assistant's `event` domain (built for stateless button-press-style
events), not `switch` (which implies a durable on/off state).
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, SWS_EVENT_NAMES
from .entity_helpers import (
    assign_friendly_names,
    ensure_area_and_label,
    extract_switch_press,
    flexom_object_id,
)
from .hemis import HemisApiClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Flexom switch events from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    hemis_client = data["hemis_client"]

    switches = await hemis_client.get_switch_sensors()
    if not switches:
        _LOGGER.info("No physical wall switches found")
        return

    assign_friendly_names(switches, "Interrupteur")
    for sensor in switches:
        ensure_area_and_label(
            hass,
            config_entry,
            DOMAIN,
            sensor["id"],
            sensor["name"],
            "Ubiant",
            "Wall Switch",
            sensor.get("zoneName"),
            "Interrupteur",
        )

    _LOGGER.info("Found %d physical wall switch(es)", len(switches))
    async_add_entities(
        FlexomSwitchEvent(coordinator=coordinator, hemis_client=hemis_client, sensor=sensor)
        for sensor in switches
    )


class FlexomSwitchEvent(CoordinatorEntity, EventEntity):
    """Representation of a Flexom physical wall switch.

    Confirmed live mapping (docs/ubiant/OBSERVED.md, 2026-07-23):
    SWS=1 light off, 2 light on, 3 shutter up, 4 shutter down, 5 interrupt.
    This mapping describes the *function* triggered by the press, which may
    not match physical button position 1:1 across different switch models -
    treat it as confirmed only for the switch model actually tested.
    """

    _attr_event_types = list(SWS_EVENT_NAMES.values())

    def __init__(
        self,
        hemis_client: HemisApiClient,
        coordinator: DataUpdateCoordinator,
        sensor: Dict[str, Any],
    ) -> None:
        """Initialize the switch event entity."""
        super().__init__(coordinator)
        self.hemis_client = hemis_client
        self.sensor = sensor

        self._id = sensor.get("id", "")
        self._it_id = sensor.get("itId", "")
        self._name = sensor.get("name", "Unknown Switch")
        self._zone_id = sensor.get("zoneId", "")
        self._zone_name = sensor.get("zoneName", "")
        self._last_timestamp = 0

        _LOGGER.debug("Initialized switch event %s (itId=%s)", self._name, self._it_id)

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return f"{DOMAIN}_{self._id}"

    @property
    def suggested_object_id(self) -> str:
        """Return the suggested entity_id suffix (technical only, not the displayed name)."""
        return flexom_object_id(self._name)

    @property
    def name(self) -> str:
        """Return the name of the switch."""
        return self._name

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._id)},
            "name": self._name,
            "manufacturer": "Ubiant",
            "model": "Wall Switch",
            "suggested_area": self._zone_name or None,
        }

    @property
    def should_poll(self) -> bool:
        """No polling needed since we're using the coordinator."""
        return False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return

        try:
            # coordinator.data holds a rolling window of recent messages
            # (see __init__.py), so without a timestamp guard the same press
            # would keep re-firing on every later, unrelated update as long
            # as it's still in that window.
            for message in reversed(self.coordinator.data):
                press_value = extract_switch_press(message, self._it_id)
                if press_value is None:
                    continue

                timestamp = message.get("timestamp", 0)
                if timestamp <= self._last_timestamp:
                    break

                self._last_timestamp = timestamp
                event_type = SWS_EVENT_NAMES.get(press_value)
                if event_type:
                    self._trigger_event(event_type)
                    self.async_write_ha_state()
                    _LOGGER.debug(
                        "Switch %s pressed: SWS=%s -> %s",
                        self._name,
                        press_value,
                        event_type,
                    )
                else:
                    _LOGGER.warning(
                        "Switch %s: unrecognized SWS value %s", self._name, press_value
                    )
                break
        except Exception as err:
            _LOGGER.error(
                "Error handling coordinator update for switch %s: %s",
                self._name,
                str(err),
                exc_info=True,
            )
