"""State sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolixConfigEntry
from .client import HolixClient
from .entity import HolixEntity

ITEM_TYPES = ["none", "movie", "episode", "trailer", "music_video", "live_tv", "audio", "video", "other"]
DYNAMIC_RANGES = ["none", "sdr", "hdr10", "hdr10_plus", "dolby_vision", "hlg"]
SEGMENTS = ["none", "intro", "outro", "recap", "preview", "commercial"]


def _resolution(c: HolixClient) -> str | None:
    v = c.playback.get("video")
    if not v or not v.get("height"):
        return None
    h = int(v["height"])
    w = int(v.get("width") or 0)
    if w >= 3800 or h >= 2100:
        return "2160p"
    for line in (1440, 1080, 720, 576, 480):
        if h >= line * 0.95 or w >= line * 16 / 9 * 0.95:
            return f"{line}p"
    return f"{h}p"


def _audio(c: HolixClient) -> str | None:
    a = c.playback.get("audio")
    if not a:
        return None
    ch = a.get("channels")
    layout = {1: "1.0", 2: "2.0", 6: "5.1", 8: "7.1"}.get(ch, str(ch) if ch else "")
    parts = [str(a.get("codec", "")).upper(), layout, "Atmos/spatial" if a.get("spatial") else ""]
    return " ".join(p for p in parts if p) or None


def _enum(value: Any, options: list[str]) -> str:
    return value if value in options else ("none" if value is None else "other" if "other" in options else "none")


@dataclass(frozen=True, kw_only=True)
class HolixSensorDescription(SensorEntityDescription):
    value: Callable[[HolixClient], Any]


SENSORS = (
    HolixSensorDescription(
        key="profile",
        value=lambda c: (c.state.get("profile") or {}).get("name"),
    ),
    HolixSensorDescription(
        key="media_kind",
        device_class=SensorDeviceClass.ENUM,
        options=ITEM_TYPES,
        value=lambda c: _enum((c.item or {}).get("type"), ITEM_TYPES),
    ),
    HolixSensorDescription(
        key="dynamic_range",
        device_class=SensorDeviceClass.ENUM,
        options=DYNAMIC_RANGES,
        value=lambda c: _enum((c.playback.get("video") or {}).get("dynamic_range"), DYNAMIC_RANGES),
    ),
    HolixSensorDescription(
        key="segment",
        device_class=SensorDeviceClass.ENUM,
        options=SEGMENTS,
        value=lambda c: _enum(c.playback.get("segment"), SEGMENTS),
    ),
    HolixSensorDescription(key="video_resolution", value=_resolution),
    HolixSensorDescription(key="audio_format", value=_audio),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HolixConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(HolixSensor(entry.runtime_data, d) for d in SENSORS)


class HolixSensor(HolixEntity, SensorEntity):
    entity_description: HolixSensorDescription

    def __init__(self, client: HolixClient, description: HolixSensorDescription) -> None:
        super().__init__(client, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self.client)
