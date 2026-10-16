"""Profile selection."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HolixConfigEntry
from .api import HolixError
from .entity import HolixEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: HolixConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.runtime_data.has("profiles"):
        async_add_entities([HolixProfileSelect(entry.runtime_data, "profile_select")])


class HolixProfileSelect(HolixEntity, SelectEntity):
    @property
    def _profiles(self) -> list[dict]:
        return [p for p in self.client.state.get("profiles") or [] if isinstance(p, dict)]

    @property
    def options(self) -> list[str]:
        return [p["name"] for p in self._profiles]

    @property
    def current_option(self) -> str | None:
        return (self.client.state.get("profile") or {}).get("name")

    async def async_select_option(self, option: str) -> None:
        match = next((p for p in self._profiles if p["name"] == option), None)
        if match is None:
            raise HomeAssistantError(f"Unknown profile: {option}")
        try:
            await self.client.command("select_profile", profile_id=match["id"])
        except HolixError as err:
            raise HomeAssistantError(f"Holix: {err}") from err
