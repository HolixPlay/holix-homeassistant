"""Holix ambilight output (the app's own LED targets) as a light: on/off + brightness."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolixConfigEntry
from .api import HolixError
from .entity import HolixEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HolixConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.runtime_data.has("ambilight"):
        async_add_entities([HolixAmbilight(entry.runtime_data, "ambilight")])


class HolixAmbilight(HolixEntity, LightEntity):
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def _amb(self) -> dict[str, Any] | None:
        return self.client.state.get("ambilight")

    @property
    def available(self) -> bool:
        return self.client.connected and self._amb is not None

    @property
    def is_on(self) -> bool:
        return bool((self._amb or {}).get("on"))

    @property
    def brightness(self) -> int | None:
        b = (self._amb or {}).get("brightness")
        return None if b is None else round(float(b) * 255)

    async def _send(self, **args: Any) -> None:
        try:
            await self.client.command("set_ambilight", **args)
        except HolixError as err:
            raise HomeAssistantError(f"Holix: {err}") from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        args: dict[str, Any] = {"on": True}
        if ATTR_BRIGHTNESS in kwargs:
            args["brightness"] = round(kwargs[ATTR_BRIGHTNESS] / 255, 3)
        await self._send(**args)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._send(on=False)
