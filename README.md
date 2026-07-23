# Flexom-HASS : Intégration Flexom pour Home Assistant

Ce projet développe une intégration Home Assistant pour les équipements Flexom de Ubiant, permettant de contrôler et surveiller votre installation domotique à travers l'interface Home Assistant.

> **À propos de ce fork** — Ce dépôt est un fork du projet original
> [flexom-hass par @ItsAlexousd](https://github.com/ItsAlexousd/flexom-hass).

## État actuel du projet

🟢 **Développé et fonctionnel**:
- Structure de base de l'intégration
- Authentification à l'API Hemisphere et Hemis
- Connexion WebSocket pour les événements en temps réel
- Support des lumières (contrôle On/Off et variation)
- Support des volets roulants (ouverture/fermeture/position)
- Support du chauffage (consigne de température)
- Remontée des appuis sur les interrupteurs physiques (entités `event`)
- Capteurs de présence/occupation par zone
- Diagnostics téléchargeables et service `flexom.reconnect_websocket`

🟡 **En cours de développement**:
- Modes de chauffage (confort/éco) au-delà de la simple consigne
- Publication HACS

## Guide d'installation

### Installation manuelle

1. **Copier les fichiers**:
   - Téléchargez ce dépôt
   - Copiez le dossier `custom_components/flexom` dans votre répertoire de configuration Home Assistant:
     - Généralement `/config/custom_components/flexom` pour une installation sous Docker ou Home Assistant OS
     - Ou `~/.homeassistant/custom_components/flexom` pour une installation manuelle

2. **Redémarrer Home Assistant**:
   - Allez dans Configuration > Système > Redémarrer

3. **Ajouter l'intégration**:
   - Allez dans Configuration > Intégrations
   - Cliquez sur "Ajouter une intégration" (bouton + en bas à droite)
   - Recherchez "Flexom-Hass"
   - Suivez les instructions pour vous connecter à votre compte Flexom

### Installation via HACS

Ajoutez ce dépôt comme dépôt personnalisé dans HACS
(`AlexisPicot/flexom-hass`, catégorie « Intégration »), puis installez
« Flexom-HASS » et redémarrez Home Assistant.

## Configuration

Lors de la configuration, vous aurez besoin de:

- Votre adresse e-mail Ubiant
- Votre mot de passe Ubiant

Ces informations servent à s'authentifier auprès des services Hemisphere et Hemis de Ubiant pour accéder à votre installation Flexom.

## Fonctionnalités

### 1. Éclairage (⚙️ opérationnel)
- Découverte automatique des lumières connectées à Flexom
- Contrôle On/Off
- Variation d'intensité (0-100%)
- Mises à jour d'état en temps réel via WebSocket

### 2. Volets roulants (⚙️ opérationnel)
- Découverte des volets connectés à Flexom
- Contrôle ouverture/fermeture et positionnement précis (0-100%)
- Mises à jour d'état en temps réel via WebSocket

### 3. Chauffage (⚙️ opérationnel)
- Découverte des thermostats/radiateurs
- Contrôle du point de consigne (température cible)
- Température courante affichée quand une sonde de zone est disponible
- 🔨 Modes (confort, éco, etc.) pas encore implémentés

### 4. Interrupteurs physiques (⚙️ opérationnel)
- Découverte des interrupteurs muraux connectés à Flexom
- Remontée de chaque appui en temps réel (entité `event` : allumer/éteindre
  la lumière, monter/descendre le volet, interrompre un mouvement en cours)

### 5. Capteurs de zone (⚙️ opérationnel)
- Présence/occupation par zone
- Luminosité, position de volet et température, en complément des entités
  dédiées ci-dessus

## Architecture technique

L'intégration communique avec deux API distinctes:

1. **API Hemisphere**: Pour l'authentification et la récupération des informations du bâtiment
   - Endpoint: `https://hemisphere.ubiant.com`

2. **API Hemis**: Pour le contrôle des appareils et la récupération des données
   - Endpoint: `https://{instance}.{region}.hemis.io/hemis/rest` (région et
     instance propres à chaque bâtiment, renvoyées par l'API Hemisphere -
     ne pas supposer `eu-west`)

3. **WebSocket Hemis (STOMP)**: Pour les événements en temps réel
   - Endpoint: URL fournie par l'API Hemisphere


## Structure du projet

```
custom_components/flexom/
├── __init__.py           # Point d'entrée : auth, coordinator, services
├── config_flow.py        # Interface de configuration
├── const.py              # Constantes, identifiants de facteurs (BRI/BRIEXT/TMP/SWS/...)
├── manifest.json         # Manifeste de l'intégration
├── hemisphere.py         # Client d'authentification (API Hemisphere)
├── hemis.py              # Client REST (API Hemis) : zones, actionneurs, écriture d'état
├── websocket.py          # Client WebSocket STOMP (événements temps réel)
├── entity_helpers.py     # Aide partagée : correspondance message WS ↔ entité, noms/zones/labels
├── light.py              # Entités Light (éclairage)
├── cover.py              # Entités Cover (volets roulants)
├── climate.py            # Entités Climate (chauffage)
├── event.py              # Entités Event (appuis sur interrupteurs physiques)
├── sensor.py             # Capteurs de zone (présence, luminosité, position, température)
├── diagnostics.py        # Diagnostics téléchargeables depuis l'UI Home Assistant
├── services.yaml         # Déclaration du service flexom.reconnect_websocket
└── translations/         # Traductions pour l'interface
    ├── en.json           # Anglais
    └── fr.json           # Français
```

## Dépannage

### Connexion à l'API

Si vous rencontrez des problèmes de connexion:
1. Vérifiez vos identifiants
2. Assurez-vous que votre instance Home Assistant a accès à Internet
3. Vérifiez les journaux Home Assistant pour des messages d'erreur spécifiques

### Problèmes avec les appareils

Si certains appareils ne s'affichent pas ou ne répondent pas:
1. Vérifiez qu'ils fonctionnent correctement dans l'application Flexom
2. Redémarrez l'intégration dans Home Assistant
3. Consultez les journaux pour des erreurs spécifiques

## Contribution

Ce projet est open source et les contributions sont les bienvenues! Pour contribuer:

1. Forker le dépôt
2. Créer une branche pour votre fonctionnalité
3. Soumettre une pull request

## Roadmap de développement

1. **Phase 1**: ✅ Structure du projet et authentification
2. **Phase 2**: ✅ Support des lumières
3. **Phase 3**: ✅ Support des volets roulants
4. **Phase 4**: ✅ Support du chauffage (consigne de température)
5. **Phase 5**: ✅ Interrupteurs physiques et capteurs de zone
6. **Phase 6**: 🔄 Publication HACS, modes de chauffage avancés, tests
   automatisés

## Ressources

- [Documentation Home Assistant pour les développeurs](https://developers.home-assistant.io/)

## Remerciements

- [@ItsAlexousd](https://github.com/ItsAlexousd) pour le projet original dont ce dépôt est un fork
- Ubiant pour la solution Flexom
- La communauté Home Assistant pour leurs outils et support

## Licence

Ce projet est sous licence MIT. Voir le fichier LICENSE pour plus de détails.
