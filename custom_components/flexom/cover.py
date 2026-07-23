"""Support for Flexom roller shutters (volets roulants)."""
from __future__ import annotations

import logging
from typing import Any, Dict

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
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, FACTOR_BRIGHTNESS_EXT
from .entity_helpers import (
    assign_friendly_names,
    ensure_area_and_label,
    extract_actuator_value,
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

    Position only - no is_opening/is_closing. There's no "movement
    finished" event on this API (confirmed live, docs/ubiant/OBSERVED.md:
    "progressive" is a static capability flag, not a moving/stopped
    indicator), so a moving-direction indicator can only ever be inferred
    heuristically from successive position readings - tried and dropped as
    unreliable in practice.
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

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return

        try:
            for message in reversed(self.coordinator.data):
                value = extract_actuator_value(message, self._it_id, FACTOR_BRIGHTNESS_EXT)
                if value is not None:
                    self._position = round(value * 100)
                    self.async_write_ha_state()
                    _LOGGER.debug(
                        "Updated cover %s from %s: position=%s",
                        self._name,
                        message.get("type"),
                        self._position,
                    )
                    break
        except Exception as err:
            _LOGGER.error(
                "Error handling coordinator update for cover %s: %s",
                self._name,
                str(err),
                exc_info=True,
            )
