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
    EVTS_CORRELATION_WINDOW_MS,
    EVTS_TO_EVENT_TYPE,
    SWS_EVENT_NAMES,
)
from .entity_helpers import (
    assign_friendly_names,
    ensure_area_and_label,
    extract_switch_press,
    extract_zone_evts,
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

    # EVTS has no itId (only a zoneId, see extract_zone_evts) - it can never
    # tell us *which* switch in a zone fired when a zone has more than one,
    # so the EVTS-only fallback trigger below is only safe to enable for
    # zones with exactly one switch.
    zone_switch_counts: Dict[str, int] = {}
    for sensor in switches:
        zone_id = sensor.get("zoneId")
        zone_switch_counts[zone_id] = zone_switch_counts.get(zone_id, 0) + 1

    _LOGGER.info("Found %d physical wall switch(es)", len(switches))
    async_add_entities(
        FlexomSwitchEvent(
            coordinator=coordinator,
            hemis_client=hemis_client,
            sensor=sensor,
            double_click_window_ms=double_click_window_ms,
            zone_has_multiple_switches=zone_switch_counts.get(sensor.get("zoneId"), 1) > 1,
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

    SWS (a pulse per physical press, confirmed always present) is the
    primary/required signal. EVTS (a named action like "BRIEXT_ON_SWS",
    confirmed *not* always present - see docs/ubiant/OBSERVED.md) is used
    two ways when it does show up:

    - If it correlates (same zone, same derived event_type, within
      EVTS_CORRELATION_WINDOW_MS) with an SWS press we're already firing,
      it's attached as a `confirmed_action` attribute - independent
      confirmation of *what got switched*, not just *which button slot* was
      pressed (relevant if the app-side wiring were ever reassigned).
    - If it shows up with no correlated SWS at all, it's used to fire the
      event on its own, purely as a safety net - but only in zones with a
      single switch (EVTS carries no itId, so a second switch in the same
      zone would make the attribution ambiguous), and never for "stop"
      (EVTS_TO_EVENT_TYPE has no entry for it - a stop is not a factor
      transition, so it could never be reported as one).
    """

    _attr_event_types = list(SWS_EVENT_NAMES.values())

    def __init__(
        self,
        hemis_client: HemisApiClient,
        coordinator: DataUpdateCoordinator,
        sensor: Dict[str, Any],
        double_click_window_ms: int = DEFAULT_DOUBLE_CLICK_WINDOW_MS,
        zone_has_multiple_switches: bool = False,
    ) -> None:
        """Initialize the switch event entity."""
        super().__init__(coordinator)
        self.hemis_client = hemis_client
        self.sensor = sensor
        self._double_click_window_ms = double_click_window_ms
        self._zone_has_multiple_switches = zone_has_multiple_switches

        self._id = sensor.get("id", "")
        self._it_id = sensor.get("itId", "")
        self._name = sensor.get("name", "Unknown Switch")
        self._zone_id = sensor.get("zoneId", "")
        self._zone_name = sensor.get("zoneName", "")
        self._last_timestamp = 0
        self._last_evts_timestamp = 0
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
        """Handle every new switch press (SWS, required) and EVTS (bonus) from the coordinator."""

        if not self.coordinator.data:
            return

        try:
            new_presses: list[tuple[int, int]] = []
            new_evts: list[tuple[int, str]] = []

            for message in self.coordinator.data:
                press_value = extract_switch_press(message, self._it_id)
                if press_value is not None:
                    timestamp = message.get("timestamp", 0)
                    if timestamp > self._last_timestamp:
                        new_presses.append((timestamp, press_value))
                    continue

                action = extract_zone_evts(message, self._zone_id)
                if action is not None:
                    timestamp = message.get("timestamp", 0)
                    if timestamp > self._last_evts_timestamp:
                        new_evts.append((timestamp, action))

            new_presses.sort(key=lambda item: item[0])
            new_evts.sort(key=lambda item: item[0])

            # Never reprocess an EVTS we've already looked at, whether or
            # not it ended up matched to an SWS below.
            if new_evts:
                self._last_evts_timestamp = new_evts[-1][0]

            # (timestamp, event_type, sws_value_or_None, confirmed_action_or_None)
            fires: list[tuple[int, Optional[str], Optional[int], Optional[str]]] = []
            consumed_evts: set[int] = set()

            for timestamp, press_value in new_presses:
                event_type = SWS_EVENT_NAMES.get(press_value)
                confirmed_action: Optional[str] = None
                for index, (evts_timestamp, action) in enumerate(new_evts):
                    if index in consumed_evts:
                        continue
                    if (
                        abs(evts_timestamp - timestamp) <= EVTS_CORRELATION_WINDOW_MS
                        and EVTS_TO_EVENT_TYPE.get(action) == event_type
                    ):
                        confirmed_action = action
                        consumed_evts.add(index)
                        break
                fires.append((timestamp, event_type, press_value, confirmed_action))

            # EVTS-only fallback: only safe when this zone has just the one
            # switch (see class docstring) - otherwise we'd be guessing
            # which entity actually got pressed.
            if not self._zone_has_multiple_switches:
                for index, (evts_timestamp, action) in enumerate(new_evts):
                    if index in consumed_evts:
                        continue
                    event_type = EVTS_TO_EVENT_TYPE.get(action)
                    if event_type is None:
                        continue
                    fires.append((evts_timestamp, event_type, None, action))

            fires.sort(key=lambda item: item[0])

            for timestamp, event_type, press_value, confirmed_action in fires:
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

                attributes: Dict[str, Any] = {"click_count": self._click_count}
                if press_value is not None:
                    attributes["sws_value"] = press_value
                if confirmed_action is not None:
                    attributes["confirmed_action"] = confirmed_action

                self._trigger_event(event_type, attributes)
                self.async_write_ha_state()

                _LOGGER.debug(
                    "Switch press fired: entity=%s timestamp=%s previous=%s "
                    "gap=%s event_type=%s click_count=%s confirmed_action=%s "
                    "source=%s",
                    self.entity_id,
                    timestamp,
                    previous_timestamp,
                    gap_ms,
                    event_type,
                    self._click_count,
                    confirmed_action,
                    "sws" if press_value is not None else "evts_fallback",
                )

        except Exception as err:
            _LOGGER.error(
                "Error handling coordinator update for switch %s: %s",
                self._name,
                err,
                exc_info=True,
            )
