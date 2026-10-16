"""In-app navigation keys (remote.send_command)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import ATTR_DELAY_SECS, ATTR_NUM_REPEATS, RemoteEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolixConfigEntry
from .api import HolixError
from .entity import HolixEntity

KEYS = {"up", "down", "left", "right", "select", "back", "home", "menu", "play_pause"}


async def async_setup_entry(
    hass: HomeAssistant, entry: HolixConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.runtime_data.has("keys"):
        async_add_entities([HolixRemote(entry.runtime_data, "remote")])


class HolixRemote(HolixEntity, RemoteEntity):
    @property
    def is_on(self) -> bool:
        return self.client.connected

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        keys = list(command)
        bad = [k for k in keys if k not in KEYS]
        if bad:
            raise ServiceValidationError(f"Unknown key: {', '.join(bad)} (valid: {', '.join(sorted(KEYS))})")
        repeats = int(kwargs.get(ATTR_NUM_REPEATS, 1))
        delay = float(kwargs.get(ATTR_DELAY_SECS, 0.4))
        try:
            for _ in range(repeats):
                for key in keys:
                    await self.client.command("key", key=key)
                    if delay:
                        await asyncio.sleep(delay)
        except HolixError as err:
            raise HomeAssistantError(f"Holix: {err}") from err
