"""Binary sensors: HDR active, screensaver."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolixConfigEntry
from .entity import HolixEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HolixConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    c = entry.runtime_data
    async_add_entities([HolixHdr(c, "hdr"), HolixScreensaver(c, "screensaver")])


class HolixHdr(HolixEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool:
        pb = self.client.playback
        dr = (pb.get("video") or {}).get("dynamic_range")
        return pb.get("state") in ("playing", "paused", "buffering") and dr not in (None, "sdr")


class HolixScreensaver(HolixEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool:
        return bool(self.client.state.get("screensaver"))
