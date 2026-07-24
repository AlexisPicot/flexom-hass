"""The Flexom integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from .hemisphere import HemisphereApiClient
from .hemis import HemisApiClient
from .websocket import HemisWebSocketClient

_LOGGER = logging.getLogger(__name__)

# List the platforms that we want to support
PLATFORMS = [
    Platform.LIGHT,
    Platform.COVER,
    Platform.CLIMATE,
    Platform.EVENT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Flexom from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    session = async_get_clientsession(hass)
    hemisphere_client = HemisphereApiClient(session)

    if not await hemisphere_client.authenticate(username, password):
        raise ConfigEntryNotReady("Failed to authenticate with Hemisphere")

    building_id = hemisphere_client.building_id
    hemis_base_url = hemisphere_client.hemis_base_url
    hemis_stomp_url = hemisphere_client.hemis_stomp_url

    if not building_id or not hemis_base_url or not hemis_stomp_url:
        raise ConfigEntryNotReady("Failed to get Hemis building info")

    _LOGGER.info("Authenticated with building ID: %s", building_id)

    hemis_client = HemisApiClient(
        session,
        hemis_base_url,
        hemisphere_client.hemis_token or hemisphere_client.hemisphere_token,
        building_id,
    )

    # Create WebSocket data queue for realtime updates
    ws_data_queue: List[Dict[str, Any]] = []

    # Create the WebSocket message handler
    @callback
    def handle_ws_message(message: Dict[str, Any]) -> None:
        """Handle messages from the WebSocket."""
        try:
            ws_data_queue.append(message)

            _LOGGER.debug(
                "WebSocket message: type=%s, entity_id=%s, factor=%s, value=%s, timestamp=%s",
                message.get("type", "unknown"),
                message.get("actuatorId") or message.get("itId") or message.get("zoneId") or "unknown",
                message.get("factorId", "unknown"),
                message.get("value", "unknown"),
                message.get("timestamp", "unknown"),
            )

            coordinator.async_set_updated_data(ws_data_queue.copy())

            # Keep only the last 50 messages
            while len(ws_data_queue) > 50:
                ws_data_queue.pop(0)

        except Exception as err:
            _LOGGER.error("Error processing WebSocket message: %s",
                          err, exc_info=True)

    # Create update coordinator first
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{building_id}",
        update_method=lambda: None,  # We don't poll, we get updates from WebSocket
    )

    ws_client = HemisWebSocketClient(
        hass,
        hemis_stomp_url,
        building_id,
        hemis_client.token,
        handle_ws_message,
    )

    if not await ws_client.connect():
        _LOGGER.warning(
            "Failed to connect to WebSocket initially. Integration will continue "
            "to setup but may not receive real-time updates. "
            "This will be retried automatically in the background."
        )
        hass.async_create_task(ws_client.reconnect())
    else:
        await ws_client.start_listening()

    hass.data[DOMAIN][entry.entry_id] = {
        "hemisphere_client": hemisphere_client,
        "hemis_client": hemis_client,
        "ws_client": ws_client,
        "coordinator": coordinator,
    }

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.info("Successfully set up platforms")
    except Exception as err:
        _LOGGER.error("Error setting up platforms: %s", err, exc_info=True)
        raise

    entry.async_on_unload(entry.add_update_listener(update_listener))

    async def _refresh_token_if_needed(_now) -> None:
        """Proactively re-authenticate before the Hemisphere token expires.

        Confirmed live: the token is a JWT valid for ~12h, and there was
        previously no refresh logic anywhere - a long-running Home Assistant
        instance would silently start failing REST calls/WebSocket
        reconnects once it expired. ensure_authenticated() is a no-op unless
        the token is actually expiring soon, so this is cheap to run often.
        """
        if await hemisphere_client.ensure_authenticated():
            new_token = hemisphere_client.hemis_token or hemisphere_client.hemisphere_token
            hemis_client.token = new_token
            ws_client.update_token(new_token)

    entry.async_on_unload(
        async_track_time_interval(hass, _refresh_token_if_needed, timedelta(minutes=15))
    )

    async def handle_reconnect_websocket(_call) -> None:
        """Manually force a WebSocket reconnect (service: flexom.reconnect_websocket)."""
        _LOGGER.info("Manual WebSocket reconnect requested via service call")
        await ws_client.reconnect()

    async def handle_run_diagnostic(_call) -> None:
        """Run an on-demand connectivity check (service: flexom.run_diagnostic).

        The old debug_api.py ran an equivalent check unconditionally on
        every single startup (extra REST calls + its own aiohttp session,
        every time, whether or not anything was wrong). Same information,
        available on demand instead.
        """
        _LOGGER.info("Running Flexom diagnostic")
        auth_ok = await hemisphere_client.ensure_authenticated()
        zones = await hemis_client.get_zones()
        actuators = await hemis_client.get_actuators()

        summary = (
            f"Hemisphere auth: {'OK' if auth_ok else 'FAILED'}\n"
            f"Zones found: {len(zones) if zones is not None else 'FAILED'}\n"
            f"Actuators found: {len(actuators) if actuators is not None else 'FAILED'}\n"
            f"WebSocket connected: {ws_client.ws is not None}\n"
            f"WebSocket listening: {ws_client.is_running}"
        )
        _LOGGER.info("Flexom diagnostic result:\n%s", summary)

        persistent_notification.async_create(
            hass,
            summary,
            title="Flexom diagnostic",
            notification_id=f"{DOMAIN}_diagnostic",
        )

    if not hass.services.has_service(DOMAIN, "reconnect_websocket"):
        hass.services.async_register(
            DOMAIN, "reconnect_websocket", handle_reconnect_websocket
        )
    if not hass.services.has_service(DOMAIN, "run_diagnostic"):
        hass.services.async_register(DOMAIN, "run_diagnostic", handle_run_diagnostic)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        ws_client = hass.data[DOMAIN][entry.entry_id]["ws_client"]
        await ws_client.disconnect()

        hass.data[DOMAIN].pop(entry.entry_id)

        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "reconnect_websocket")

    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
