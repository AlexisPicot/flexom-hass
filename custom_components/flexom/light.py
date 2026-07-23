"""Support for Flexom lights."""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, FACTOR_BRIGHTNESS
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
    """Set up Flexom Light from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    hemis_client = data["hemis_client"]

    light_actuators = await hemis_client.get_light_actuators()
    if not light_actuators:
        _LOGGER.info("No light actuators found")
        return

    assign_friendly_names(light_actuators, "Éclairage")
    for actuator in light_actuators:
        ensure_area_and_label(
            hass,
            config_entry,
            DOMAIN,
            actuator["id"],
            actuator["name"],
            "Ubiant",
            actuator.get("typeName") or "Light Actuator",
            actuator.get("zoneName"),
            "Éclairage",
        )

    _LOGGER.info("Found %d light actuator(s)", len(light_actuators))
    async_add_entities(
        FlexomLight(coordinator=coordinator, hemis_client=hemis_client, actuator=actuator)
        for actuator in light_actuators
    )

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("identify", {}, "async_identify")


class FlexomLight(CoordinatorEntity, LightEntity):
    """Representation of a Flexom light.

    On/off only: confirmed the Flexom lights in this install don't support
    dimming, despite the BRI factor's value being a 0.0-1.0 float. If a
    dimmable model ever needs supporting, the brightness handling below is
    kept commented out (not deleted) rather than rewritten from scratch.
    """

    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(
        self,
        hemis_client: HemisApiClient,
        coordinator: DataUpdateCoordinator,
        actuator: Dict[str, Any],
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)
        self.hemis_client = hemis_client
        self.actuator = actuator

        self._id = actuator.get("id", "")
        self._it_id = actuator.get("itId", "")
        self._name = actuator.get("name", "Unknown Light")
        self._zone_id = actuator.get("zoneId", "")
        self._zone_name = actuator.get("zoneName", "")
        self._type_name = actuator.get("typeName", "")
        self._is_on = False
        # self._brightness = 0  # 0-100 scale; see brightness property below

        # BRI actuator value is 0.0-1.0 (confirmed live, docs/ubiant/OBSERVED.md).
        for state in actuator.get("states", []):
            if state.get("factorId") == FACTOR_BRIGHTNESS:
                self._is_on = float(state.get("value", 0)) > 0
                # self._brightness = float(state.get("value", 0)) * 100
                break

        _LOGGER.debug("Initialized light %s (on: %s)", self._name, self._is_on)

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
        """Return the name of the light."""
        return self._name

    @property
    def is_on(self) -> bool:
        """Return true if light is on."""
        return self._is_on

    # @property
    # def brightness(self) -> int:
    #     """Return the brightness of this light between 0..255."""
    #     return round(self._brightness * 255 / 100)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._id)},
            "name": self._name,
            "manufacturer": "Ubiant",
            "model": self._type_name or "Light Actuator",
            "suggested_area": self._zone_name or None,
        }

    @property
    def should_poll(self) -> bool:
        """No polling needed since we're using the coordinator."""
        return False

    async def async_turn_on(self, **_kwargs: Any) -> None:
        """Turn the light on."""
        # brightness = _kwargs.get(ATTR_BRIGHTNESS)
        # brightness_value = (
        #     round(brightness * 100 / 255) if brightness is not None else 100
        # )
        _LOGGER.debug("Turning on light %s", self._name)

        success = await self.hemis_client.set_light_state(self._it_id, self._id, True)

        if success:
            self._is_on = True
            # self._brightness = brightness_value
            self.async_write_ha_state()
        else:
            _LOGGER.error("Failed to turn on light %s", self._name)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        """Turn the light off."""
        _LOGGER.debug("Turning off light %s", self._name)

        success = await self.hemis_client.set_light_state(self._it_id, self._id, False)

        if success:
            self._is_on = False
            # self._brightness = 0
            self.async_write_ha_state()
        else:
            _LOGGER.error("Failed to turn off light %s", self._name)

    async def async_identify(self) -> None:
        """Flash the light a few times so it can be spotted physically.

        No native "identify"/"locate" endpoint exists on the Hemis API
        (checked docs/ubiant/swagger.json) - this is the light-only
        workaround: toggle it a few times, then restore its original state.
        Registered as the `flexom.identify` entity service.
        """
        original_state = self._is_on
        for _ in range(3):
            await self.hemis_client.set_light_state(self._it_id, self._id, not self._is_on)
            self._is_on = not self._is_on
            self.async_write_ha_state()
            await asyncio.sleep(0.5)

        if self._is_on != original_state:
            await self.hemis_client.set_light_state(self._it_id, self._id, original_state)
            self._is_on = original_state
            self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator.

        Zone-level FACTOR_TARGET_STATE is deliberately not used here:
        confirmed live that it can stay stale while the real actuator moves
        (see OBSERVED.md), and it can't distinguish between two lights in
        the same zone anyway. extract_actuator_value() only matches
        per-device ACTUATOR_* messages instead.
        """
        if not self.coordinator.data:
            return

        try:
            for message in reversed(self.coordinator.data):
                value = extract_actuator_value(message, self._it_id, FACTOR_BRIGHTNESS)
                if value is not None:
                    self._is_on = value > 0
                    # self._brightness = value * 100
                    self.async_write_ha_state()
                    _LOGGER.debug(
                        "Updated light %s from %s: state=%s",
                        self._name,
                        message.get("type"),
                        self._is_on,
                    )
                    break
        except Exception as err:
            _LOGGER.error(
                "Error handling coordinator update for light %s: %s",
                self._name,
                str(err),
                exc_info=True,
            )
