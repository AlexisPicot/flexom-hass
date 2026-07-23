"""Support for Flexom heating (thermostats)."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, FACTOR_TEMPERATURE
from .entity_helpers import (
    assign_friendly_names,
    ensure_area_and_label,
    extract_actuator_value,
    extract_sensor_value,
    flexom_object_id,
)
from .hemis import HemisApiClient

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Flexom Climate from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = data["coordinator"]
    hemis_client = data["hemis_client"]

    climate_actuators = await hemis_client.get_climate_actuators()
    if not climate_actuators:
        _LOGGER.info("No heating actuators found")
        return

    assign_friendly_names(climate_actuators, "Chauffage")
    for actuator in climate_actuators:
        ensure_area_and_label(
            hass,
            config_entry,
            DOMAIN,
            actuator["id"],
            actuator["name"],
            "Ubiant",
            actuator.get("typeName") or "Heating Actuator",
            actuator.get("zoneName"),
            "Chauffage",
        )

    entities = []
    for actuator in climate_actuators:
        # A separate physical TMP sensor (not the actuator itself) may exist
        # in the same zone and report the actual room temperature, distinct
        # from the actuator's target setpoint. Its itId is captured too so
        # _handle_coordinator_update can keep current_temperature live via
        # extract_sensor_value(), not just at setup.
        current_temperature = None
        sensor_it_id = None
        zone_sensors = await hemis_client.get_zone_factors(actuator["zoneId"])
        for sensor in zone_sensors or []:
            state = sensor.get("state", {})
            if state.get("id") == FACTOR_TEMPERATURE:
                sensor_it_id = sensor.get("itId")
                try:
                    current_temperature = float(state.get("value"))
                except (TypeError, ValueError):
                    pass
                break

        entities.append(
            FlexomClimate(
                coordinator=coordinator,
                hemis_client=hemis_client,
                actuator=actuator,
                current_temperature=current_temperature,
                sensor_it_id=sensor_it_id,
            )
        )

    _LOGGER.info("Found %d heating actuator(s)", len(entities))
    async_add_entities(entities)


class FlexomClimate(CoordinatorEntity, ClimateEntity):
    """Representation of a Flexom heating actuator.

    Exposes target temperature only. There's no confirmed signal for an
    "off" mode in the data captured so far (docs/ubiant/OBSERVED.md), so
    hvac_mode is fixed to HEAT.
    """

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(
        self,
        hemis_client: HemisApiClient,
        coordinator: DataUpdateCoordinator,
        actuator: Dict[str, Any],
        current_temperature: Optional[float],
        sensor_it_id: Optional[str],
    ) -> None:
        """Initialize the climate entity."""
        super().__init__(coordinator)
        self.hemis_client = hemis_client
        self.actuator = actuator

        self._id = actuator.get("id", "")
        self._it_id = actuator.get("itId", "")
        self._sensor_it_id = sensor_it_id
        self._name = actuator.get("name", "Unknown Thermostat")
        self._zone_id = actuator.get("zoneId", "")
        self._zone_name = actuator.get("zoneName", "")
        self._type_name = actuator.get("typeName", "")
        self._current_temperature = current_temperature
        self._target_temperature = None

        for state in actuator.get("states", []):
            if state.get("factorId") == FACTOR_TEMPERATURE:
                self._target_temperature = state.get("value")
                break

        if actuator.get("min_value") is not None:
            self._attr_min_temp = actuator["min_value"]
        if actuator.get("max_value") is not None:
            self._attr_max_temp = actuator["max_value"]

        _LOGGER.debug(
            "Initialized climate %s: target=%s current=%s",
            self._name,
            self._target_temperature,
            self._current_temperature,
        )

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
        """Return the name of the climate entity."""
        return self._name

    @property
    def current_temperature(self) -> Optional[float]:
        """Return the current room temperature, if known."""
        return self._current_temperature

    @property
    def target_temperature(self) -> Optional[float]:
        """Return the target temperature."""
        return self._target_temperature

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._id)},
            "name": self._name,
            "manufacturer": "Ubiant",
            "model": self._type_name or "Heating Actuator",
            "suggested_area": self._zone_name or None,
        }

    @property
    def should_poll(self) -> bool:
        """No polling needed since we're using the coordinator."""
        return False

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set the target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        _LOGGER.debug("Setting temperature for %s to %s", self._name, temperature)
        success = await self.hemis_client.set_temperature(self._it_id, self._id, temperature)

        if success:
            self._target_temperature = temperature
            self.async_write_ha_state()
        else:
            _LOGGER.error("Failed to set temperature for %s", self._name)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self.coordinator.data:
            return

        try:
            for message in reversed(self.coordinator.data):
                target = extract_actuator_value(message, self._it_id, FACTOR_TEMPERATURE)
                if target is not None:
                    self._target_temperature = target
                    self.async_write_ha_state()
                    _LOGGER.debug(
                        "Updated climate %s from %s: target=%s",
                        self._name,
                        message.get("type"),
                        self._target_temperature,
                    )
                    break

                if self._sensor_it_id:
                    current = extract_sensor_value(message, self._sensor_it_id, FACTOR_TEMPERATURE)
                    if current is not None:
                        self._current_temperature = current
                        self.async_write_ha_state()
                        _LOGGER.debug(
                            "Updated climate %s current temperature: %s",
                            self._name,
                            current,
                        )
                        break
        except Exception as err:
            _LOGGER.error(
                "Error handling coordinator update for climate %s: %s",
                self._name,
                str(err),
                exc_info=True,
            )
