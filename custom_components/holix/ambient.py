"""Use HA lights as ambilight targets: `colors` stream → light.turn_on (low rate).

Not meant for real-time ambilight (a Hue bridge handles ~10 commands/s in total, Zigbee networks are similar);
2 fps with a matching transition time gives a soft "ambient" effect. Fast LEDs (WLED, Holix Light) use
the app's own DDP path.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant, callback

from .api import HolixError
from .client import HolixClient
from .const import (
    CONF_AMBIENT_FPS,
    CONF_AMBIENT_LIGHTS,
    DEFAULT_AMBIENT_FPS,
    MAX_AMBIENT_FPS,
)

_LOGGER = logging.getLogger(__name__)

MIN_DELTA = 12  # per channel on the 0–255 scale; smaller changes are not sent


def to_light_params(rgb: list[int]) -> tuple[tuple[int, int, int], int]:
    """[r,g,b] → (color at full brightness, brightness 0–255)."""
    r, g, b = (max(0, min(255, int(c))) for c in rgb[:3])
    peak = max(r, g, b)
    if peak == 0:
        return (0, 0, 0), 0
    scale = 255 / peak
    return (round(r * scale), round(g * scale), round(b * scale)), peak


class AmbientBridge:
    def __init__(self, hass: HomeAssistant, client: HolixClient, options: Mapping[str, Any]) -> None:
        self.hass = hass
        self.client = client
        self.lights: dict[str, str] = dict(options.get(CONF_AMBIENT_LIGHTS) or {})
        self.fps = max(1, min(MAX_AMBIENT_FPS, int(options.get(CONF_AMBIENT_FPS, DEFAULT_AMBIENT_FPS))))
        self._last_sent = 0.0
        self._last_rgb: dict[str, list[int]] = {}
        self._unsub = None

    async def async_start(self) -> None:
        if not self.lights:
            return
        self._unsub = self.client.add_color_listener(self._on_colors)
        try:
            await self.client.set_color_subscription(self.fps)
        except HolixError as err:
            _LOGGER.debug("could not subscribe to colors: %s", err)

    async def async_stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
            try:
                await self.client.set_color_subscription(0)
            except HolixError:
                pass

    @callback
    def _on_colors(self, zones: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_sent < 1 / self.fps - 0.01:
            return
        self._last_sent = now
        for entity_id, zone in self.lights.items():
            rgb = zones.get(zone) or zones.get("average")
            if not isinstance(rgb, list) or len(rgb) < 3:
                continue
            last = self._last_rgb.get(entity_id)
            if last is not None and max(abs(a - b) for a, b in zip(last, rgb[:3])) < MIN_DELTA:
                continue
            self._last_rgb[entity_id] = list(rgb[:3])
            color, brightness = to_light_params(rgb)
            data: dict[str, Any] = {"entity_id": entity_id, "transition": round(1 / self.fps, 2)}
            if brightness == 0:
                data["brightness"] = 1
            else:
                data["rgb_color"] = color
                data["brightness"] = brightness
            self.hass.async_create_task(
                self.hass.services.async_call("light", "turn_on", data, blocking=False)
            )
