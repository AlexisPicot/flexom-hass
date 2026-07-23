"""Hemis API client."""
import logging
import urllib.parse
from typing import Any, Dict, List, Optional

import aiohttp
import asyncio

from .const import (
    FACTOR_BRIGHTNESS,
    FACTOR_BRIGHTNESS_EXT,
    FACTOR_SWITCH,
    FACTOR_TEMPERATURE,
)

_LOGGER = logging.getLogger(__name__)


class HemisApiClient:
    """Client for interacting with the Hemis API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        hemis_base_url: str,
        token: str,
        buildingId: str
    ) -> None:
        """Initialize the API client."""
        self.session = session
        self.hemis_base_url = hemis_base_url
        self.token = token
        self.buildingId = buildingId

    async def get_zones(self) -> List[Dict[str, Any]]:
        """Get all zones."""
        return await self._api_call("/zones")

    async def get_zone_factors(self, zone_id: str) -> List[Dict[str, Any]]:
        """Get all factors for a zone."""
        return await self._api_call(f"/intelligent-things/sensors?zoneId={zone_id}")

    async def get_sensors(self) -> List[Dict[str, Any]]:
        """Get all sensors."""
        return await self._api_call("/intelligent-things/sensors")

    async def get_actuators(self) -> List[Dict[str, Any]]:
        """Get all actuators (global list; NOT zone-scoped in the current API - use
        get_light_actuators/get_cover_actuators/get_climate_actuators for that)."""
        return await self._api_call("/intelligent-things/actuators")

    async def get_switch_sensors(self) -> List[Dict[str, Any]]:
        """Get all physical wall switches, across all zones.

        Confirmed live (docs/ubiant/OBSERVED.md): a wall switch shows up as a
        *sensor* (state.id == "SWS") via the zone-scoped
        /intelligent-things/sensors?zoneId=... endpoint, not as an actuator.
        A zone can have more than one (e.g. one per light + one per shutter).
        """
        zones = await self.get_zones()
        if zones is None:
            _LOGGER.error("Failed to get zones, cannot list switch sensors")
            return []

        results: List[Dict[str, Any]] = []
        for zone in zones:
            zone_id = zone.get("id")
            zone_name = zone.get("name", zone_id)
            sensors = await self.get_zone_factors(zone_id)
            for sensor in sensors or []:
                state = sensor.get("state", {})
                if state.get("id") != FACTOR_SWITCH:
                    continue
                results.append(
                    {
                        "id": sensor.get("id"),
                        "itId": sensor.get("itId"),
                        "zoneId": zone_id,
                        # Zone name, not the manufacturer device name (which is
                        # unhelpful boilerplate, e.g. "Interrupteur 2T ECL -
                        # VR Blanches EIKON / Support Noir / Sans Plaque 2").
                        # Platforms turn this into a friendly per-role name via
                        # entity_helpers.assign_friendly_names(); zoneName is
                        # kept as-is (unmutated) for HA area assignment.
                        "name": zone_name,
                        "zoneName": zone_name,
                    }
                )

        _LOGGER.debug(
            "Found %d switch sensor(s): %s",
            len(results),
            [f"{s['id']} - {s['name']}" for s in results],
        )
        return results

    @staticmethod
    def _encode(value: str) -> str:
        """URL-encode an itId/actuatorId for use as a path segment.

        These IDs routinely contain '#', '%' and ':' (e.g. "8512264_C0",
        "io:%%1001-1152-9999%8512264"). Left unencoded, aiohttp/the HTTP
        client treats '#' as a fragment separator and '%' as an escape
        sequence lead-in, silently truncating or mangling the path.
        """
        return urllib.parse.quote(value, safe="")

    async def _get_actuators_by_factor(self, factor_id: str) -> List[Dict[str, Any]]:
        """Get all actuators driving `factor_id`, across all zones.

        There is no single endpoint for this in the current API: actuators
        returned by /intelligent-things/actuators carry no zoneId, so we
        instead ask each zone individually via the zone-scoped endpoint
        GET /WS_ReactiveEnvironmentDataManagement/{zoneId}/{factorId}/actuators
        (404 there just means "no such actuator in this zone").
        """
        zones = await self.get_zones()
        if zones is None:
            _LOGGER.error("Failed to get zones, cannot list actuators for factor %s", factor_id)
            return []

        results: List[Dict[str, Any]] = []
        for zone in zones:
            zone_id = zone.get("id")
            zone_name = zone.get("name", zone_id)
            actuators = await self._api_call(
                f"/WS_ReactiveEnvironmentDataManagement/{zone_id}/{factor_id}/actuators",
                quiet_404=True,
            )
            for actuator in actuators or []:
                state = actuator.get("state", {})
                results.append(
                    {
                        "id": actuator.get("actuatorId"),
                        "itId": state.get("itId"),
                        "zoneId": zone_id,
                        # Zone name; platforms turn this into a friendly
                        # per-role name via
                        # entity_helpers.assign_friendly_names(); zoneName is
                        # kept as-is (unmutated) for HA area assignment.
                        "name": zone_name,
                        "zoneName": zone_name,
                        "typeName": None,
                        "states": [{"factorId": factor_id, "value": state.get("value")}],
                        # For TMP this is the real valid temperature range
                        # (confirmed live: 7.0/30.0 on a real thermostat), for
                        # BRI/BRIEXT it's unrelated (looks like a travel-time
                        # bound) and not currently used by those platforms.
                        "min_value": state.get("minActionValue"),
                        "max_value": state.get("maxActionValue"),
                    }
                )

        _LOGGER.debug(
            "Found %d actuator(s) for factor %s: %s",
            len(results),
            factor_id,
            [f"{a['id']} - {a['name']}" for a in results],
        )
        return results

    async def get_light_actuators(self) -> List[Dict[str, Any]]:
        """Get all light actuators (factor BRI), across all zones."""
        return await self._get_actuators_by_factor(FACTOR_BRIGHTNESS)

    async def get_cover_actuators(self) -> List[Dict[str, Any]]:
        """Get all cover/shutter actuators (factor BRIEXT), across all zones."""
        return await self._get_actuators_by_factor(FACTOR_BRIGHTNESS_EXT)

    async def get_climate_actuators(self) -> List[Dict[str, Any]]:
        """Get all heating actuators (factor TMP), across all zones."""
        return await self._get_actuators_by_factor(FACTOR_TEMPERATURE)

    async def _set_actuator_value(
        self, it_id: str, actuator_id: str, value: float, progressive: bool
    ) -> bool:
        """PUT a new value for one actuator.

        Confirmed live (docs/ubiant/OBSERVED.md, 2026-07-23): the endpoint is
        `/intelligent-things/{itId}/actuator/{actuatorId}/state` (NOT
        `/intelligent-things/actuators/{actuatorId}/states/{factorId}`, which
        no longer exists - it 404s/405s). Success is 204 No Content with an
        empty body, not a JSON object.

        Some itIds (older devices) contain literal semicolons, e.g.
        "UBID1507C;051EC499;D2-01-12_C0". Confirmed live: the backend
        (RESTEasy) treats ';' as a matrix-parameter delimiter in a path
        segment regardless of percent-encoding, so it silently truncates
        the itId at the first ';' and 404s ("No intelligent thing could be
        found for id 'UBID1507C'"). For those, fall back to the bulk
        endpoint `/intelligent-things/actuators/state`, which takes the
        itId/actuatorId in the JSON body instead of the URL path - confirmed
        live to work for exactly this case.
        """
        if ";" in it_id:
            return await self._set_actuator_value_bulk(it_id, actuator_id, value)

        data = {"value": value, "progressive": progressive}
        result = await self._api_call(
            f"/intelligent-things/{self._encode(it_id)}/actuator/{self._encode(actuator_id)}/state",
            method="PUT",
            data=data,
        )
        # 204 responses are surfaced by _api_call as {} (see the PUT branch
        # below), so any dict (including empty) means success.
        return isinstance(result, dict)

    async def _set_actuator_value_bulk(
        self, it_id: str, actuator_id: str, value: float
    ) -> bool:
        """Fallback for itIds the per-itId path endpoint can't route to.

        Confirmed live: instant (non-progressive) writes only - this fallback
        is only exercised by lights so far (semicolon itIds seen only there),
        not shutters, so progressive-transition behavior hasn't been tested
        here and isn't attempted.
        """
        data = {
            "pref": {"value": value, "duration": 0},
            "actuators": [{"itId": it_id, "actuatorIds": [actuator_id]}],
        }
        result = await self._api_call(
            "/intelligent-things/actuators/state", method="PUT", data=data
        )
        return isinstance(result, dict)

    async def set_light_state(
        self, it_id: str, actuator_id: str, state: bool, brightness: Optional[int] = None
    ) -> bool:
        """Set light on/off and brightness.

        `brightness` is on Home Assistant's usual 0-100 scale; the actual BRI
        actuator value is 0.0-1.0 (confirmed live: a fully-on light reads
        value=1.0, not 100), so it's converted here.
        """
        brightness_pct = 100 if state and brightness is None else (
            brightness if brightness is not None else 0)
        return await self._set_actuator_value(it_id, actuator_id, brightness_pct / 100, progressive=False)

    async def set_cover_position(self, it_id: str, actuator_id: str, position: int) -> bool:
        """Set cover position, 0 (closed) - 100 (fully open).

        Like BRI, the actual BRIEXT actuator value is 0.0-1.0, confirmed live
        by closing/opening a real shutter (docs/ubiant/OBSERVED.md).
        """
        return await self._set_actuator_value(it_id, actuator_id, position / 100, progressive=True)

    async def set_temperature(self, it_id: str, actuator_id: str, temperature: float) -> bool:
        """Set a heating actuator's target temperature.

        Unlike BRI/BRIEXT, TMP actuators take a real degrees-Celsius value
        directly (confirmed live: minActionValue/maxActionValue on a real
        thermostat were 7.0/30.0, i.e. the valid temperature range, not a
        0.0-1.0 fraction or a duration).
        """
        return await self._set_actuator_value(it_id, actuator_id, temperature, progressive=False)

    async def _api_call(self, endpoint, method="GET", data=None, headers=None, quiet_404=False):
        """Make an API call to the Hemis API."""
        url = f"{self.hemis_base_url}{endpoint}"
        _LOGGER.debug("API call: %s %s", method, url)

        headers = headers or {}
        headers.update({
            "Authorization": f"Bearer {self.token}",
            "Building-Id": self.buildingId,
            "Content-Type": "application/json",
        })

        # Print the first 10 characters of the token for debugging
        _LOGGER.debug("Using token for API call: %s...",
                      self.token[:10] if self.token else "None")

        # Add longer timeout for potentially slow servers
        timeout = 30

        for retry in range(5):  # Increase retries to 5
            try:
                if method == "GET":
                    _LOGGER.debug(
                        "Sending GET request to %s (attempt %s/5)", url, retry + 1)
                    async with self.session.get(
                        url, headers=headers, timeout=timeout
                    ) as response:
                        if response.status == 404 and quiet_404:
                            # Expected for e.g. "this zone has no actuator for
                            # this factor" - not a real error, don't spam logs
                            # or retry.
                            _LOGGER.debug("Nothing found at %s (404)", url)
                            return None
                        if response.status != 200:
                            _LOGGER.error(
                                "Error calling %s: %s %s",
                                url,
                                response.status,
                                response.reason,
                            )
                            # Handle 401 Unauthorized - could be token expiration
                            if response.status == 401 and retry < 4:
                                wait_time = (retry + 1) * 2
                                _LOGGER.warning(
                                    "Received 401 Unauthorized, retrying in %s seconds (attempt %s/5)",
                                    wait_time,
                                    retry + 1
                                )
                                # Could add token refresh logic here
                                await asyncio.sleep(wait_time)
                                continue
                            # If we got a 502, retry after a delay with longer timeout
                            if response.status == 502 and retry < 4:
                                # Increase wait time
                                wait_time = (retry + 1) * 5
                                timeout += 10  # Increase timeout each retry
                                _LOGGER.warning(
                                    "Received 502 Bad Gateway, retrying in %s seconds with timeout=%s (attempt %s/5)",
                                    wait_time,
                                    timeout,
                                    retry + 1
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            return None

                        # Log success
                        _LOGGER.debug("Successful response from %s", url)

                        # Parse JSON response
                        try:
                            data = await response.json()
                            _LOGGER.debug("Parsed JSON response from %s: %d items", url, len(
                                data) if isinstance(data, list) else 1)
                            return data
                        except Exception as err:
                            _LOGGER.error(
                                "Error parsing JSON from %s: %s", url, err)
                            return None
                elif method == "POST":
                    _LOGGER.debug(
                        "Sending POST request to %s (attempt %s/5)", url, retry + 1)
                    async with self.session.post(
                        url, headers=headers, json=data, timeout=timeout
                    ) as response:
                        if response.status != 200:
                            _LOGGER.error(
                                "Error calling %s: %s %s",
                                url,
                                response.status,
                                response.reason,
                            )
                            # Handle 401 Unauthorized - could be token expiration
                            if response.status == 401 and retry < 4:
                                wait_time = (retry + 1) * 2
                                _LOGGER.warning(
                                    "Received 401 Unauthorized, retrying in %s seconds (attempt %s/5)",
                                    wait_time,
                                    retry + 1
                                )
                                # Could add token refresh logic here
                                await asyncio.sleep(wait_time)
                                continue
                            # If we got a 502, retry after a delay with longer timeout
                            if response.status == 502 and retry < 4:
                                # Increase wait time
                                wait_time = (retry + 1) * 5
                                timeout += 10  # Increase timeout each retry
                                _LOGGER.warning(
                                    "Received 502 Bad Gateway, retrying in %s seconds with timeout=%s (attempt %s/5)",
                                    wait_time,
                                    timeout,
                                    retry + 1
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            return None

                        # Log success
                        _LOGGER.debug("Successful POST response from %s", url)

                        # Parse JSON response
                        try:
                            data = await response.json()
                            _LOGGER.debug(
                                "Parsed JSON response from POST to %s", url)
                            return data
                        except Exception as err:
                            _LOGGER.error(
                                "Error parsing JSON from POST to %s: %s", url, err)
                            return None
                elif method == "PUT":
                    _LOGGER.debug(
                        "Sending PUT request to %s (attempt %s/5)", url, retry + 1)
                    async with self.session.put(
                        url, headers=headers, json=data, timeout=timeout
                    ) as response:
                        # 204 No Content is the actual success response for
                        # the actuator-state PUT endpoint - it must be
                        # checked before the generic error path below, or a
                        # successful write is logged as an error and treated
                        # as a failure (this was previously dead code: the
                        # `== 204` branch further down was unreachable).
                        if response.status == 204:
                            _LOGGER.debug(
                                "No content in PUT response (status 204)")
                            return {}
                        if response.status != 200:
                            _LOGGER.error(
                                "Error calling %s: %s %s",
                                url,
                                response.status,
                                response.reason,
                            )
                            # Handle 401 Unauthorized - could be token expiration
                            if response.status == 401 and retry < 4:
                                wait_time = (retry + 1) * 2
                                _LOGGER.warning(
                                    "Received 401 Unauthorized, retrying in %s seconds (attempt %s/5)",
                                    wait_time,
                                    retry + 1
                                )
                                # Could add token refresh logic here
                                await asyncio.sleep(wait_time)
                                continue
                            # If we got a 502, retry after a delay with longer timeout
                            if response.status == 502 and retry < 4:
                                # Increase wait time
                                wait_time = (retry + 1) * 5
                                timeout += 10  # Increase timeout each retry
                                _LOGGER.warning(
                                    "Received 502 Bad Gateway, retrying in %s seconds with timeout=%s (attempt %s/5)",
                                    wait_time,
                                    timeout,
                                    retry + 1
                                )
                                await asyncio.sleep(wait_time)
                                continue
                            return None

                        # Log success
                        _LOGGER.debug("Successful PUT response from %s", url)

                        try:
                            data = await response.json()
                            _LOGGER.debug(
                                "Parsed JSON response from PUT to %s", url)
                            return data
                        except Exception as err:
                            _LOGGER.error(
                                "Error parsing JSON from PUT to %s: %s", url, err)
                            return None
            except asyncio.TimeoutError:
                _LOGGER.error(
                    "Timeout calling %s (attempt %s/5)", url, retry + 1)
                if retry < 4:
                    wait_time = (retry + 1) * 5
                    timeout += 10  # Increase timeout each retry
                    _LOGGER.warning(
                        "Increasing timeout to %s seconds for next attempt", timeout)
                    await asyncio.sleep(wait_time)
                    continue
                return None
            except (aiohttp.ClientError, asyncio.exceptions.CancelledError) as err:
                _LOGGER.error(
                    "Error calling %s: %s (attempt %s/5)", url, err, retry + 1)
                if retry < 4:
                    wait_time = (retry + 1) * 5
                    await asyncio.sleep(wait_time)
                    continue
                return None

        # If we got here, all retries failed
        return None
