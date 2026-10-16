"""Playback events as an event entity (automation trigger)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolixConfigEntry
from .const import EVENT_TYPES
from .entity import HolixEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HolixConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([HolixEvent(entry.runtime_data, "playback_event")])


def _flatten(data: dict[str, Any]) -> dict[str, Any]:
    item = data.get("item") or {}
    video = data.get("video") or {}
    profile = data.get("profile") or {}
    out = {
        "item_id": item.get("id"),
        "item_type": item.get("type"),
        "title": item.get("title"),
        "series_title": item.get("series_title"),
        "dynamic_range": video.get("dynamic_range"),
        "profile": profile.get("name"),
        "profile_kind": profile.get("kind"),
        "position": data.get("position"),
        "reason": data.get("reason"),
        "segment": data.get("segment"),
    }
    return {k: v for k, v in out.items() if v is not None}


class HolixEvent(HolixEntity, EventEntity):
    _attr_event_types = EVENT_TYPES

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.client.add_event_listener(self._on_event))

    @callback
    def _on_event(self, event: str, data: dict[str, Any]) -> None:
        if event not in EVENT_TYPES:
            return
        self._trigger_event(event, _flatten(data))
        self.async_write_ha_state()
