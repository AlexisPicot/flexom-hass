"""Support for Flexom physical wall switches.

A wall switch press is momentary, not a persistent on/off state (that's
exactly what triggers BRI/BRIEXT actuator changes elsewhere) - so this uses
Home Assistant's `event` domain (built for stateless button-press-style
events), not `switch` (which implies a durable on/off state).

One `event` entity is exposed per physical switch (not one per button):
each switch has up to 5 distinct actions, reported as different event_types
on the same entity - see FlexomSwitchEvent's docstring.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    CONF_DOUBLE_CLICK_WINDOW_MS,
    DEFAULT_DOUBLE_CLICK_WINDOW_MS,
    DOMAIN,
    SWS_EVENT_NAMES,
)
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

    double_click_window_ms = config_entry.options.get(
        CONF_DOUBLE_CLICK_WINDOW_MS, DEFAULT_DOUBLE_CLICK_WINDOW_MS
    )

    _LOGGER.info("Found %d physical wall switch(es)", len(switches))
    async_add_entities(
        FlexomSwitchEvent(
            coordinator=coordinator,
            hemis_client=hemis_client,
            sensor=sensor,
            double_click_window_ms=double_click_window_ms,
        )
        for sensor in switches
    )


class FlexomSwitchEvent(CoordinatorEntity, EventEntity):
    """Representation of a Flexom physical wall switch.

    Confirmed live mapping (docs/ubiant/OBSERVED.md, 2026-07-23), positional
    (describes the physical button, not the Ubiant action wired to it -
    that wiring is configured in the Flexom app, not something we control):

        SWS=1 top_left     (wired to: light off)
        SWS=2 bottom_left   (wired to: light on)
        SWS=3 top_right    (wired to: shutter open)
        SWS=4 bottom_right  (wired to: shutter closed)
        SWS=5 stop          (both buttons on one side pressed together)

    This mapping was confirmed on one switch model - physical button
    position may not match 1:1 across different models, only the reported
    SWS value -> slot mapping is what's actually confirmed.

    Every press fires its event_type immediately, including consecutive
    presses of the *same* button (no "only if different from before"
    logic - the device/coordinator pipeline has no batching, confirmed
    live). The device itself has no notion of a "double click": consecutive
    presses of the *same* button within `double_click_window_ms` are
    counted here and exposed as a `click_count` attribute on the event
    (accessible in automations via `trigger.event.data.click_count`), so a
    double/triple click can be told apart from repeated single clicks. The
    first click is always emitted right away, at click_count=1 - detecting
    a double-click never delays it.
    """

    _attr_event_types = list(SWS_EVENT_NAMES.values())

    def __init__(
        self,
        hemis_client: HemisApiClient,
        coordinator: DataUpdateCoordinator,
        sensor: Dict[str, Any],
        double_click_window_ms: int = DEFAULT_DOUBLE_CLICK_WINDOW_MS,
    ) -> None:
        """Initialize the switch event entity."""
        super().__init__(coordinator)
        self.hemis_client = hemis_client
        self.sensor = sensor
        self._double_click_window_ms = double_click_window_ms

        self._id = sensor.get("id", "")
        self._it_id = sensor.get("itId", "")
        self._name = sensor.get("name", "Unknown Switch")
        self._zone_id = sensor.get("zoneId", "")
        self._zone_name = sensor.get("zoneName", "")
        self._last_timestamp = 0
        self._last_event_type: Optional[str] = None
        self._click_count = 0

        _LOGGER.debug("Initialized switch event %s (itId=%s)",
                      self._name, self._it_id)

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
        """Handle every new switch press from the coordinator."""

        if not self.coordinator.data:
            return

        try:
            new_presses: list[tuple[int, int]] = []

            for message in self.coordinator.data:
                press_value = extract_switch_press(message, self._it_id)
                if press_value is None:
                    continue

                timestamp = message.get("timestamp", 0)
                if timestamp <= self._last_timestamp:
                    continue

                new_presses.append((timestamp, press_value))

            # Toujours traiter les clics dans l'ordre réel.
            new_presses.sort(key=lambda item: item[0])

            for timestamp, press_value in new_presses:
                event_type = SWS_EVENT_NAMES.get(press_value)
                gap_ms = timestamp - self._last_timestamp

                if (
                    event_type is not None
                    and event_type == self._last_event_type
                    and self._last_timestamp != 0
                    and gap_ms <= self._double_click_window_ms
                ):
                    self._click_count += 1
                else:
                    self._click_count = 1

                previous_timestamp = self._last_timestamp
                self._last_timestamp = timestamp
                self._last_event_type = event_type

                if event_type is None:
                    _LOGGER.warning(
                        "Switch %s: unrecognized SWS value %s",
                        self._name,
                        press_value,
                    )
                    continue

                self._trigger_event(
                    event_type,
                    {
                        "click_count": self._click_count,
                        "sws_value": press_value,
                    },
                )
                self.async_write_ha_state()

                _LOGGER.debug(
                    "Switch press fired: entity=%s timestamp=%s previous=%s "
                    "gap=%s event_type=%s click_count=%s",
                    self.entity_id,
                    timestamp,
                    previous_timestamp,
                    gap_ms,
                    event_type,
                    self._click_count,
                )

        except Exception as err:
            _LOGGER.error(
                "Error handling coordinator update for switch %s: %s",
                self._name,
                err,
                exc_info=True,
            )
