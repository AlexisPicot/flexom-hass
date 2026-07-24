"""Support for Flexom roller shutters (volets roulants)."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import (
    COVER_MOVEMENT_SETTLE_SECONDS,
    DOMAIN,
    EVTS_BRIEXT_OFF,
    EVTS_BRIEXT_ON,
    FACTOR_BRIGHTNESS_EXT,
)
from .entity_helpers import (
    assign_friendly_names,
    ensure_area_and_label,
    extract_actuator_value,
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
    """Set up Flexom Cover from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    hemis_client = data["hemis_client"]

    cover_actuators = await hemis_client.get_cover_actuators()
    if not cover_actuators:
        _LOGGER.info("No cover (shutter) actuators found")
        return

    assign_friendly_names(cover_actuators, "Volet")
    for actuator in cover_actuators:
        ensure_area_and_label(
            hass,
            config_entry,
            DOMAIN,
            actuator["id"],
            actuator["name"],
            "Ubiant",
            actuator.get("typeName") or "Shutter Actuator",
            actuator.get("zoneName"),
            "Volet",
        )

    _LOGGER.info("Found %d cover actuator(s)", len(cover_actuators))
    async_add_entities(
        FlexomCover(coordinator=coordinator, hemis_client=hemis_client, actuator=actuator)
        for actuator in cover_actuators
    )


class FlexomCover(CoordinatorEntity, CoverEntity):
    """Representation of a Flexom roller shutter.

    is_opening/is_closing are inferred, not reported directly - there's no
    "movement finished" (or "movement started") event on this API (confirmed
    live, docs/ubiant/OBSERVED.md: "progressive" is a static capability
    flag, not a moving/stopped indicator). Direction is known from whichever
    of these fires first:

    1. We issued the move ourselves (async_set_cover_position): direction is
       exact, known immediately from target vs. current position.
    2. An EVTS message (BRIEXT_ON_SWS/BRIEXT_OFF_SWS) arrives for our zone -
       confirmed live but *not* reliable (docs/ubiant/OBSERVED.md: doesn't
       fire for every physical press), so treated as a bonus signal, not a
       requirement.
    3. Neither of the above (e.g. a physical press with no EVTS): fall back
       to comparing the new BRIEXT position against the last known one.

    Once direction is known, is_opening/is_closing stays set with *no*
    timeout - there's no telling in advance how long a shutter takes to
    travel, and clearing on a guessed delay would just be wrong sometimes.
    Instead it clears once we've actually observed the position diverge
    from what it was when the movement started, debounced by
    COVER_MOVEMENT_SETTLE_SECONDS (so a still-moving shutter, whose BRIEXT
    keeps ticking every few seconds, doesn't get marked "finished" after
    its very first tick away from the start position).
    """

    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_supported_features = CoverEntityFeature.SET_POSITION

    def __init__(
        self,
        hemis_client: HemisApiClient,
        coordinator: DataUpdateCoordinator,
        actuator: Dict[str, Any],
    ) -> None:
        """Initialize the cover."""
        super().__init__(coordinator)
        self.hemis_client = hemis_client
        self.actuator = actuator

        self._id = actuator.get("id", "")
        self._it_id = actuator.get("itId", "")
        self._name = actuator.get("name", "Unknown Shutter")
        self._zone_id = actuator.get("zoneId", "")
        self._zone_name = actuator.get("zoneName", "")
        self._type_name = actuator.get("typeName", "")
        # BRIEXT actuator value is 0.0-1.0 (confirmed live, docs/ubiant/OBSERVED.md);
        # 0 = closed, 1 = fully open, matching HA's current_cover_position scale
        # directly once multiplied by 100 - no inversion needed.
        self._position = 0
        self._movement: Optional[str] = None  # "opening" | "closing" | None
        self._movement_settle_cancel: Optional[Callable[[], None]] = None
        self._last_evts_timestamp = 0
        self._last_position_timestamp = 0

        for state in actuator.get("states", []):
            if state.get("factorId") == FACTOR_BRIGHTNESS_EXT:
                self._position = round(float(state.get("value", 0)) * 100)
                break

        _LOGGER.debug("Initialized cover %s at position %s", self._name, self._position)

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
        """Return the name of the cover."""
        return self._name

    @property
    def current_cover_position(self) -> int:
        """Return the current position (0 closed - 100 fully open)."""
        return self._position

    @property
    def is_closed(self) -> bool:
        """Return true if the cover is closed."""
        return self._position <= 0

    @property
    def is_opening(self) -> bool:
        """Return true if the cover is currently opening (heuristic, see class docstring)."""
        return self._movement == "opening"

    @property
    def is_closing(self) -> bool:
        """Return true if the cover is currently closing (heuristic, see class docstring)."""
        return self._movement == "closing"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._id)},
            "name": self._name,
            "manufacturer": "Ubiant",
            "model": self._type_name or "Shutter Actuator",
            "suggested_area": self._zone_name or None,
        }

    @property
    def should_poll(self) -> bool:
        """No polling needed since we're using the coordinator."""
        return False

    async def async_open_cover(self, **_kwargs: Any) -> None:
        """Open the cover fully."""
        await self.async_set_cover_position(position=100)

    async def async_close_cover(self, **_kwargs: Any) -> None:
        """Close the cover fully."""
        await self.async_set_cover_position(position=0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move the cover to a specific position."""
        position = kwargs.get(ATTR_POSITION)
        if position is None:
            return

        _LOGGER.debug("Setting cover %s to position %s", self._name, position)
        success = await self.hemis_client.set_cover_position(self._it_id, self._id, position)

        if not success:
            _LOGGER.error("Failed to set position for cover %s", self._name)
            return

        # We know the direction with certainty here - we're the one asking
        # for it. Don't start the settle timer yet though: it hasn't
        # actually moved from self._position until a real BRIEXT update
        # says so, however long that takes.
        if position > self._position:
            self._movement = "opening"
        elif position < self._position:
            self._movement = "closing"
        if self._movement is not None:
            _LOGGER.debug(
                "Cover %s movement=%s (source=command, %s->%s)",
                self._name, self._movement, self._position, position,
            )
        self.async_write_ha_state()

    @callback
    def _reset_movement_settle(self) -> None:
        """(Re)start the short debounce that clears is_opening/is_closing.

        Only called once a real position divergence has been observed -
        see _handle_coordinator_update. Every further divergence pushes
        this back, so is_opening/is_closing only clears once the position
        has actually stopped changing for COVER_MOVEMENT_SETTLE_SECONDS.
        """
        if self._movement_settle_cancel is not None:
            self._movement_settle_cancel()
        self._movement_settle_cancel = async_call_later(
            self.hass, COVER_MOVEMENT_SETTLE_SECONDS, self._movement_settled
        )

    @callback
    def _movement_settled(self, _now: Any) -> None:
        """Position stopped changing - consider the movement finished."""
        _LOGGER.debug(
            "Cover %s movement settled (was %s) - clearing to idle",
            self._name, self._movement,
        )
        self._movement_settle_cancel = None
        self._movement = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel any pending settle timer before the entity goes away."""
        await super().async_will_remove_from_hass()
        if self._movement_settle_cancel is not None:
            self._movement_settle_cancel()
            self._movement_settle_cancel = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return

        try:
            # EVTS: bonus signal, not required - see class docstring. No
            # itId on this message type, only a zoneId, so this can't tell
            # us *which* actuator in the zone moved - fine here since each
            # zone only ever has one BRIEXT actuator, unlike switches.
            evts_changed = False
            newest_evts_timestamp = self._last_evts_timestamp
            for message in self.coordinator.data:
                action = extract_zone_evts(message, self._zone_id)
                if action is None:
                    continue
                timestamp = message.get("timestamp", 0)
                if timestamp <= self._last_evts_timestamp:
                    continue
                newest_evts_timestamp = max(newest_evts_timestamp, timestamp)
                if action == EVTS_BRIEXT_ON:
                    self._movement = "opening"
                    evts_changed = True
                elif action == EVTS_BRIEXT_OFF:
                    self._movement = "closing"
                    evts_changed = True
                else:
                    continue
                _LOGGER.debug(
                    "Cover %s movement=%s (source=evts, action=%s)",
                    self._name, self._movement, action,
                )
            self._last_evts_timestamp = newest_evts_timestamp

            # Newest matching message first; if even that one is already
            # seen, there's nothing new in this batch at all (this cover's
            # entity gets _handle_coordinator_update called on *every*
            # coordinator update, including ones about unrelated devices -
            # confirmed live, docs/ubiant/OBSERVED.md - so this guard is
            # what keeps a stale re-match of the same old message from
            # being treated as a fresh reading each time).
            position_changed = False
            for message in reversed(self.coordinator.data):
                value = extract_actuator_value(message, self._it_id, FACTOR_BRIGHTNESS_EXT)
                if value is None:
                    continue
                timestamp = message.get("timestamp", 0)
                if timestamp <= self._last_position_timestamp:
                    break

                self._last_position_timestamp = timestamp
                new_position = round(value * 100)
                diverged = new_position != self._position

                if diverged and self._movement is None:
                    # No EVTS and we didn't order this ourselves - only
                    # signal left is the position delta itself.
                    self._movement = "opening" if new_position > self._position else "closing"
                    _LOGGER.debug(
                        "Cover %s movement=%s (source=position_delta, %s->%s)",
                        self._name, self._movement, self._position, new_position,
                    )

                self._position = new_position
                position_changed = True
                _LOGGER.debug(
                    "Updated cover %s from %s: position=%s",
                    self._name,
                    message.get("type"),
                    self._position,
                )

                if diverged and self._movement is not None:
                    self._reset_movement_settle()
                break

            if position_changed or evts_changed:
                self.async_write_ha_state()
        except Exception as err:
            _LOGGER.error(
                "Error handling coordinator update for cover %s: %s",
                self._name,
                str(err),
                exc_info=True,
            )
