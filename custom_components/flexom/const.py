"""Constants for Flexom integration."""
from typing import Final

DOMAIN: Final = "flexom"

# Configuration
CONF_USERNAME: Final = "username"
CONF_PASSWORD: Final = "password"
CONF_DOUBLE_CLICK_WINDOW_MS: Final = "double_click_window_ms"
DEFAULT_DOUBLE_CLICK_WINDOW_MS: Final = 500

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
# session du 2026-07-23) : chaque appui envoie un pulse valeur -> 0. Nommage
# positionnel (top_left/bottom_left/top_right/bottom_right/stop) plutôt que
# fonctionnel (light_off/light_on/...) : décrit ce que le bouton physique
# fait, indépendamment de l'action Ubiant qui y est câblée.
SWS_TOP_LEFT: Final = 1  # éteindre la lumière
SWS_BOTTOM_LEFT: Final = 2  # allumer la lumière
SWS_TOP_RIGHT: Final = 3  # ouvrir le volet
SWS_BOTTOM_RIGHT: Final = 4  # fermer le volet
SWS_STOP: Final = 5  # appui simultané des deux boutons d'un même côté

SWS_EVENT_NAMES: Final = {
    SWS_TOP_LEFT: "top_left",
    SWS_BOTTOM_LEFT: "bottom_left",
    SWS_TOP_RIGHT: "top_right",
    SWS_BOTTOM_RIGHT: "bottom_right",
    SWS_STOP: "stop",
}
