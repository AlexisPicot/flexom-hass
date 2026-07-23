"""Constants for Flexom integration."""
from typing import Final

DOMAIN: Final = "flexom"

# Configuration
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"

# API URLs
HEMISPHERE_URL: Final = "https://hemisphere.ubiant.com"
HEMISPHERE_SIGNIN_URL: Final = "/users/signin"
HEMISPHERE_BUILDINGS_URL: Final = "/buildings/mine/infos"

# WebSocket topics. Only STOMP_TOPIC_DATA is subscribed to (websocket.py) -
# STOMP_TOPIC_MANAGEMENT (structural changes: zone/device add/remove) exists
# on the server (see docs/ubiant/ws.md) but isn't consumed anywhere yet.
STOMP_TOPIC_DATA: Final = "jms.topic.{building_id}.data"

# Factors
FACTOR_BRIGHTNESS: Final = "BRI"  # Luminosité
FACTOR_BRIGHTNESS_EXT: Final = "BRIEXT"  # Occultation (volets)
FACTOR_TEMPERATURE: Final = "TMP"  # Température
FACTOR_SWITCH: Final = "SWS"  # Appui sur un interrupteur physique

# Valeurs du facteur SWS, confirmées empiriquement (docs/ubiant/OBSERVED.md,
# session du 2026-07-23) : chaque appui envoie un pulse valeur -> 0.
SWS_LIGHT_OFF: Final = 1
SWS_LIGHT_ON: Final = 2
SWS_SHUTTER_UP: Final = 3
SWS_SHUTTER_DOWN: Final = 4
SWS_INTERRUPT: Final = 5

SWS_EVENT_NAMES: Final = {
    SWS_LIGHT_OFF: "light_off",
    SWS_LIGHT_ON: "light_on",
    SWS_SHUTTER_UP: "shutter_up",
    SWS_SHUTTER_DOWN: "shutter_down",
    SWS_INTERRUPT: "interrupt",
}
