"""Holix player as a media_player."""

from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

import voluptuous as vol

from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature as F,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolixConfigEntry
from .api import HolixError
from .entity import HolixEntity

SERVICE_NOTIFY = "show_notification"

STATE_MAP = {
    "playing": MediaPlayerState.PLAYING,
    "paused": MediaPlayerState.PAUSED,
    "buffering": MediaPlayerState.BUFFERING,
    "idle": MediaPlayerState.IDLE,
}
TYPE_MAP = {
    "movie": MediaType.MOVIE,
    "episode": MediaType.EPISODE,
    "trailer": MediaType.VIDEO,
    "music_video": MediaType.VIDEO,
    "live_tv": MediaType.CHANNEL,
    "audio": MediaType.MUSIC,
    "video": MediaType.VIDEO,
}


async def async_setup_entry(
    hass: HomeAssistant, entry: HolixConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([HolixMediaPlayer(entry.runtime_data)])
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_NOTIFY,
        {
            vol.Required("message"): cv.string,
            vol.Optional("title"): cv.string,
            vol.Optional("duration"): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        },
        "async_show_notification",
    )


async def _call(coro) -> Any:
    try:
        return await coro
    except HolixError as err:
        raise HomeAssistantError(f"Holix: {err}") from err


class HolixMediaPlayer(HolixEntity, MediaPlayerEntity):
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_name = None
    _attr_media_image_remotely_accessible = False

    def __init__(self, client) -> None:
        super().__init__(client, "player")

    @property
    def available(self) -> bool:
        return True  # shown as OFF while disconnected (app closed / in background)

    @property
    def supported_features(self) -> F:
        feats = (
            F.PLAY | F.PAUSE | F.STOP | F.NEXT_TRACK | F.PREVIOUS_TRACK | F.SEEK
        )
        if self.client.has("volume"):
            feats |= F.VOLUME_SET | F.VOLUME_MUTE
        if self.client.has("play_item"):
            feats |= F.PLAY_MEDIA
        return feats

    @property
    def state(self) -> MediaPlayerState:
        if not self.client.connected:
            return MediaPlayerState.OFF
        return STATE_MAP.get(self.client.playback.get("state", "idle"), MediaPlayerState.IDLE)

    @property
    def _item(self) -> dict[str, Any]:
        return (self.client.item or {}) if self.client.connected else {}

    @property
    def media_content_id(self) -> str | None:
        return self._item.get("id")

    @property
    def media_content_type(self) -> str | None:
        t = self._item.get("type")
        return TYPE_MAP.get(t, MediaType.VIDEO) if t else None

    @property
    def media_title(self) -> str | None:
        return self._item.get("title")

    @property
    def media_series_title(self) -> str | None:
        return self._item.get("series_title")

    @property
    def media_season(self) -> str | None:
        s = self._item.get("season")
        return None if s is None else str(s)

    @property
    def media_episode(self) -> str | None:
        e = self._item.get("episode")
        return None if e is None else str(e)

    @property
    def media_image_url(self) -> str | None:
        return self._item.get("image_url")

    @property
    def media_duration(self) -> float | None:
        return self.client.playback.get("duration") if self._item else None

    @property
    def media_position(self) -> float | None:
        return self.client.playback.get("position") if self._item else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        ms = self.client.playback.get("position_at")
        if not self._item or ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, UTC)

    @property
    def volume_level(self) -> float | None:
        return self.client.playback.get("volume")

    @property
    def is_volume_muted(self) -> bool | None:
        return self.client.playback.get("muted")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        pb = self.client.playback if self.client.connected else {}
        profile = self.client.state.get("profile") if self.client.connected else None
        return {
            "profile": profile.get("name") if profile else None,
            "dynamic_range": (pb.get("video") or {}).get("dynamic_range"),
            "segment": pb.get("segment"),
        }

    async def async_media_play(self) -> None:
        await _call(self.client.command("play"))

    async def async_media_pause(self) -> None:
        await _call(self.client.command("pause"))

    async def async_media_stop(self) -> None:
        await _call(self.client.command("stop"))

    async def async_media_next_track(self) -> None:
        await _call(self.client.command("next"))

    async def async_media_previous_track(self) -> None:
        await _call(self.client.command("previous"))

    async def async_media_seek(self, position: float) -> None:
        await _call(self.client.command("seek", position=position))

    async def async_set_volume_level(self, volume: float) -> None:
        await _call(self.client.command("set_volume", level=volume))

    async def async_mute_volume(self, mute: bool) -> None:
        await _call(self.client.command("mute", muted=mute))

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        await _call(self.client.command("play_item", item_id=media_id))

    async def async_show_notification(
        self, message: str, title: str | None = None, duration: int | None = None
    ) -> None:
        args: dict[str, Any] = {"message": message}
        if title:
            args["title"] = title
        if duration:
            args["duration"] = duration
        await _call(self.client.command("notify", **args))
