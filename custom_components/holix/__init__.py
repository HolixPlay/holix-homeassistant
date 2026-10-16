"""Holix Play — Home Assistant integration (HLCP v1, local push)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from .ambient import AmbientBridge
from .api import DeviceInfo, HolixAuthError, HolixError
from .client import HolixClient
from .const import CONF_DEVICE_ID, DOMAIN, MANUFACTURER, PLATFORMS

type HolixConfigEntry = ConfigEntry[HolixClient]


async def async_setup_entry(hass: HomeAssistant, entry: HolixConfigEntry) -> bool:
    placeholder = DeviceInfo(
        id=entry.data[CONF_DEVICE_ID], name=entry.title, model="", platform="", app_version=""
    )
    client = HolixClient(hass, entry, placeholder)
    try:
        await client.async_connect_once()
    except HolixAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except HolixError as err:
        raise ConfigEntryNotReady(f"Could not connect to Holix device: {err}") from err

    dev = client.device
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, dev.id)},
        manufacturer=MANUFACTURER,
        name=dev.name,
        model=f"Holix Play ({dev.platform})",
        model_id=dev.model,
        sw_version=dev.app_version,
    )
    client.device_registry_id = device.id
    entry.runtime_data = client

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    bridge = AmbientBridge(hass, client, entry.options)
    await bridge.async_start()
    entry.async_on_unload(bridge.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_reload))

    client.start()
    return True


async def _async_reload(hass: HomeAssistant, entry: HolixConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HolixConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return ok
