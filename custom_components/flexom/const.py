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
# sessions du 2026-07-23/24) : chaque appui envoie un pulse valeur -> 0.
# Nommage positionnel (top_left/bottom_left/top_right/bottom_right/release)
# plutôt que fonctionnel (light_off/light_on/...) : décrit ce que le bouton
# physique fait, indépendamment de l'action Ubiant qui y est câblée.
SWS_TOP_LEFT: Final = 1  # éteindre la lumière
SWS_BOTTOM_LEFT: Final = 2  # allumer la lumière
SWS_TOP_RIGHT: Final = 3  # ouvrir le volet
SWS_BOTTOM_RIGHT: Final = 4  # fermer le volet
SWS_RELEASE_TOP: Final = 5  # appui simultané des deux boutons du HAUT
SWS_RELEASE_BOTTOM: Final = 6  # appui simultané des deux boutons du BAS

SWS_EVENT_NAMES: Final = {
    SWS_TOP_LEFT: "top_left",
    SWS_BOTTOM_LEFT: "bottom_left",
    SWS_TOP_RIGHT: "top_right",
    SWS_BOTTOM_RIGHT: "bottom_right",
    SWS_RELEASE_TOP: "release_top",
    SWS_RELEASE_BOTTOM: "release_bottom",
}

FACTOR_EVENTS: Final = "EVTS"  # Nom de l'action déclenchée par un interrupteur

# Valeurs EVTS confirmées empiriquement (docs/ubiant/OBSERVED.md, sessions
# 2026-07-23/24) : contrairement à SWS, EVTS ne se déclenche PAS à chaque
# appui (conditionné, semble-t-il, à une vraie transition d'état de
# l'actionneur visé) - à traiter comme un signal bonus, jamais requis.
EVTS_BRI_OFF: Final = "BRI_OFF_SWS"
EVTS_BRI_ON: Final = "BRI_ON_SWS"
EVTS_BRIEXT_ON: Final = "BRIEXT_ON_SWS"  # volet : ouverture commandée
EVTS_BRIEXT_OFF: Final = "BRIEXT_OFF_SWS"  # volet : fermeture commandée

# Mapping EVTS -> event_type SWS_EVENT_NAMES correspondant, pour pouvoir
# aussi déclencher un event.py depuis un EVTS seul (pas seulement en
# confirmation d'un SWS déjà traité). Pas d'entrée pour "release" : ce n'est
# pas une transition de facteur, EVTS ne peut donc jamais le représenter.
EVTS_TO_EVENT_TYPE: Final = {
    EVTS_BRI_OFF: SWS_EVENT_NAMES[SWS_TOP_LEFT],
    EVTS_BRI_ON: SWS_EVENT_NAMES[SWS_BOTTOM_LEFT],
    EVTS_BRIEXT_ON: SWS_EVENT_NAMES[SWS_TOP_RIGHT],
    EVTS_BRIEXT_OFF: SWS_EVENT_NAMES[SWS_BOTTOM_RIGHT],
}

# Combien de temps sans nouveau tick de position avant de considérer qu'un
# volet a fini de bouger, une fois qu'une divergence a déjà été constatée
# (voir cover.py). Doit rester nettement au-dessus de l'écart réel entre
# deux ticks BRIEXT pendant un vrai trajet (confirmé live : jusqu'à ~9-11s
# d'écart, docs/ubiant/OBSERVED.md) sous peine de déclarer le mouvement fini
# entre deux ticks légitimes.
COVER_MOVEMENT_SETTLE_SECONDS: Final = 10

# Fenêtre de corrélation entre un SWS et un EVTS décrivant potentiellement le
# même appui (les deux n'arrivent jamais exactement au même timestamp, cf.
# docs/ubiant/OBSERVED.md).
EVTS_CORRELATION_WINDOW_MS: Final = 3000
