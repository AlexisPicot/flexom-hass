"""Diagnostics support for Flexom.

Powers Home Assistant's built-in "Download diagnostics" button on the
integration's config entry page - surfaces connection/runtime state without
needing to dig through home-assistant.log.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {"username", "password"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    hemisphere_client = data["hemisphere_client"]
    ws_client = data["ws_client"]
    coordinator = data["coordinator"]

    recent_messages = coordinator.data or []

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "hemisphere": {
            "building_id": hemisphere_client.building_id,
            "hemis_base_url": hemisphere_client.hemis_base_url,
            "hemis_stomp_url": hemisphere_client.hemis_stomp_url,
        },
        "websocket": {
            "connected": ws_client.ws is not None,
            "listening": ws_client.is_running,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "buffered_message_count": len(recent_messages),
            # Full raw payloads, not just a summary, since this is exactly
            # the kind of thing worth attaching to a bug report about a
            # specific event type/value not behaving as expected.
            "recent_messages": recent_messages[-20:],
        },
    }
