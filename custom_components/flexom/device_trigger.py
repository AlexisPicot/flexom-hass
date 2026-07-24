"""Device triggers for Flexom physical wall switches.

Built directly on `async_track_state_change_event`, not the generic
`homeassistant.components.homeassistant.triggers.state` helper that most
integrations' device triggers delegate to (see e.g. `button/device_trigger.py`).
That helper has this guard (homeassistant/components/homeassistant/triggers/state.py):

    if attribute is not None and old_value == new_value:
        return

i.e. a "state" trigger filtering on an attribute only fires when the
attribute's VALUE changes. For our switches, `event_type` stays the same
across repeated presses of the *same* button even though a brand new press
really did happen each time (confirmed live) - so that guard would silently
swallow every second-in-a-row identical press. Listening to the raw
state_changed event ourselves and checking `event_type` directly avoids that.
"""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.event.const import ATTR_EVENT_TYPE
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_ENTITY_ID,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HassJob,
    HomeAssistant,
    callback,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo

from .const import DOMAIN, SWS_EVENT_NAMES

TRIGGER_TYPES = tuple(dict.fromkeys(SWS_EVENT_NAMES.values()))

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_ENTITY_ID): cv.entity_id_or_uuid,
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
    }
)


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """List device triggers for a Flexom switch device.

    One trigger per possible event_type, per `event` entity belonging to
    this device (normally exactly one such entity per switch device).
    """
    registry = er.async_get(hass)
    triggers: list[dict[str, Any]] = []
    for entry in er.async_entries_for_device(registry, device_id):
        if entry.domain != "event" or entry.platform != DOMAIN:
            continue
        triggers.extend(
            {
                CONF_PLATFORM: "device",
                CONF_DEVICE_ID: device_id,
                CONF_DOMAIN: DOMAIN,
                CONF_ENTITY_ID: entry.id,
                CONF_TYPE: trigger_type,
            }
            for trigger_type in TRIGGER_TYPES
        )
    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: dict,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a Flexom switch device trigger for one specific event_type."""
    config = TRIGGER_SCHEMA(config)
    registry = er.async_get(hass)
    entity_id = er.async_resolve_entity_id(registry, config[CONF_ENTITY_ID])
    if entity_id is None:
        entity_id = config[CONF_ENTITY_ID]
    trigger_type = config[CONF_TYPE]

    job = HassJob(action, f"Flexom switch device trigger {trigger_type}")
    trigger_data = trigger_info["trigger_data"]

    @callback
    def _handle_state_change(event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        if new_state is None or new_state.attributes.get(ATTR_EVENT_TYPE) != trigger_type:
            return
        hass.async_run_hass_job(
            job,
            {
                "trigger": {
                    **trigger_data,
                    "click_count": new_state.attributes.get("click_count", 1),
                    "platform": "device",
                    "entity_id": entity_id,
                    "device_id": config[CONF_DEVICE_ID],
                    "domain": DOMAIN,
                    "type": trigger_type,
                    "description": f'Flexom switch "{trigger_type}"',
                }
            },
            event.context,
        )

    return async_track_state_change_event(hass, [entity_id], _handle_state_change)
