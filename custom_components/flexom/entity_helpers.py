"""Shared helpers for matching WebSocket coordinator messages to a specific device.

Kept separate from hemis.py (REST/service layer) and the platform files
(HA entity layer) since every actuator-backed entity (light, cover, climate)
needs the exact same message-matching logic.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import label_registry as lr
from homeassistant.util import slugify


def flexom_object_id(name: str) -> str:
    """Return the entity_id suffix to use for `name`, prefixed with "flexom_".

    This only affects the technical entity_id (e.g.
    light.flexom_chambre_2_eclairage) via each entity's `suggested_object_id`
    override - the displayed friendly_name is untouched (stays e.g.
    "Chambre 2 Éclairage", no "Flexom" in it), matching HA's guidance against
    stuttering the integration name into display names.
    """
    return f"flexom_{slugify(name)}"


def ensure_area_and_label(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    domain: str,
    unique_id: str,
    name: str,
    manufacturer: str,
    model: str,
    zone_name: Optional[str],
    role_label: str,
) -> None:
    """Make sure a device is in the right Area and carries the role Label.

    DeviceInfo's `suggested_area` only assigns an Area the *first* time a
    device is created (confirmed in device_registry.py's
    async_get_or_create: the auto-create-area branch only runs when
    `is_new`) - it's silently ignored for a device that already exists
    (e.g. from an earlier test run before this integration set
    suggested_area at all). DeviceInfo also has no "labels" field, full
    stop - labels can only be set via direct registry calls. So both need
    to be applied explicitly here rather than left to the passive
    DeviceInfo mechanism alone.
    """
    if not zone_name:
        return

    label_registry = lr.async_get(hass)
    label = label_registry.async_get_label_by_name(role_label) or label_registry.async_create(
        role_label
    )

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(domain, unique_id)},
        name=name,
        manufacturer=manufacturer,
        model=model,
        suggested_area=zone_name,
    )

    updates: Dict[str, Any] = {}
    if not device.area_id:
        area = ar.async_get(hass).async_get_or_create(zone_name)
        updates["area_id"] = area.id
    if label.label_id not in device.labels:
        updates["labels"] = device.labels | {label.label_id}
    if updates:
        device_registry.async_update_device(device.id, **updates)


def assign_friendly_names(devices: List[Dict[str, Any]], label: str) -> None:
    """Mutate `devices` in place: name = "{zone name} {label}".

    hemis.py sets each device's "name" to its zone's name (the manufacturer
    device names it also has access to are unhelpful boilerplate like
    "Relais canal 1 #1 5" or "Interrupteur 2T ECL - VR Blanches EIKON /
    Support Noir / Sans Plaque 2"). When a zone has more than one device of
    the same role (e.g. two switches in "Chambre 2"), a numeric suffix is
    added so entities stay distinguishable.
    """
    by_zone: Dict[str, List[Dict[str, Any]]] = {}
    for device in devices:
        by_zone.setdefault(device.get("zoneId"), []).append(device)

    for zone_devices in by_zone.values():
        zone_name = zone_devices[0].get("name")
        if len(zone_devices) == 1:
            zone_devices[0]["name"] = f"{zone_name} {label}"
        else:
            for index, device in enumerate(zone_devices, start=1):
                device["name"] = f"{zone_name} {label} {index}"

ACTUATOR_MESSAGE_TYPES = (
    "ACTUATOR_TARGET_STATE",
    "ACTUATOR_HARDWARE_STATE",
    "ACTUATOR_CURRENT_STATE",
)


def extract_actuator_value(message: Dict[str, Any], it_id: str, factor_id: str) -> Optional[float]:
    """Return the actuator's new value if `message` is an update for it, else None.

    ACTUATOR_TARGET_STATE/ACTUATOR_HARDWARE_STATE/ACTUATOR_CURRENT_STATE
    messages identify the actuator via a top-level "itId", and their "value"
    is a *nested* object (confirmed live, docs/ubiant/OBSERVED.md), e.g.:

        {"type": "ACTUATOR_CURRENT_STATE", "itId": "...", "factorId": "BRIEXT",
         "value": {"itId": "...", "actuatorId": "...", "value": 0.86, ...}}

    the real reading is message["value"]["value"], not message["value"].
    """
    if (
        message.get("type") not in ACTUATOR_MESSAGE_TYPES
        or message.get("itId") != it_id
        or message.get("factorId") != factor_id
    ):
        return None
    nested = message.get("value")
    if not isinstance(nested, dict):
        return None
    value = nested.get("value")
    return float(value) if value is not None else None


def extract_sensor_value(message: Dict[str, Any], it_id: str, factor_id: str) -> Optional[float]:
    """Return a sensor's new value if `message` is a SENSOR_STATE update for it, else None.

    Same shape as extract_switch_press but generalized to any factor (used
    for the temperature *sensor* reading, as opposed to the heating
    *actuator*'s target setpoint which extract_actuator_value already
    covers).
    """
    if (
        message.get("type") != "SENSOR_STATE"
        or message.get("itId") != it_id
        or message.get("factorId") != factor_id
    ):
        return None
    value = message.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def extract_switch_press(message: Dict[str, Any], it_id: str) -> Optional[int]:
    """Return the SWS press value (1-5) if `message` is a press from this switch, else None.

    Confirmed live (docs/ubiant/OBSERVED.md): a physical switch press is a
    SENSOR_STATE message carrying the switch's own "itId" (unique per
    physical switch - a zone can have more than one), e.g.:

        {"type": "SENSOR_STATE", "itId": "04B99FEAD45480",
         "sensorId": "00317685#0_C0", "factorId": "SWS", "value": 3}

    Each press is followed almost immediately by a value=0 "release" pulse
    on the same itId/factorId - that's not a press and must be ignored,
    hence returning None (not 0) for it.
    """
    if (
        message.get("type") != "SENSOR_STATE"
        or message.get("itId") != it_id
        or message.get("factorId") != "SWS"
    ):
        return None
    value = message.get("value")
    try:
        value_int = int(value)
    except (TypeError, ValueError):
        return None
    return value_int if value_int != 0 else None
