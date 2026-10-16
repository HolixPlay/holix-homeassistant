"""Holix constants."""

from homeassistant.const import Platform

DOMAIN = "holix"
MANUFACTURER = "IORA"
ZEROCONF_TYPE = "_holixplay._tcp.local."

CONF_CLIENT_ID = "client_id"
CONF_TOKEN = "token"
CONF_DEVICE_ID = "device_id"
CONF_PATH = "path"

# Options: use HA lights as ambilight targets
CONF_AMBIENT_LIGHTS = "ambient_lights"  # {"light.living_room": "left", ...}
CONF_AMBIENT_FPS = "ambient_fps"
DEFAULT_AMBIENT_FPS = 2
MAX_AMBIENT_FPS = 10
ZONES = ["average", "left", "right", "top", "bottom", "center"]

EVENT_HOLIX = "holix_event"

EVENT_TYPES = [
    "playback_started",
    "playback_paused",
    "playback_resumed",
    "playback_stopped",
    "segment_started",
    "segment_ended",
    "profile_changed",
    "screensaver_started",
    "screensaver_stopped",
]

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.LIGHT,
    Platform.MEDIA_PLAYER,
    Platform.REMOTE,
    Platform.SELECT,
    Platform.SENSOR,
]
