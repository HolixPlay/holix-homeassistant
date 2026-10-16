"""Layer that keeps the device connection alive and fans state out to HA."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    CLOSE_REVOKED,
    DeviceInfo,
    HolixAuthError,
    HolixConnection,
    HolixError,
)
from .const import CONF_CLIENT_ID, CONF_PATH, CONF_TOKEN, EVENT_HOLIX

_LOGGER = logging.getLogger(__name__)

BACKOFF = (1, 2, 5, 10, 30, 60)


class HolixClient:
    """One config entry = one Holix device."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, device: DeviceInfo) -> None:
        self.hass = hass
        self.entry = entry
        self.device = device
        self.device_registry_id: str | None = None
        self.conn: HolixConnection | None = None
        self.state: dict[str, Any] = {}
        self.connected = False
        self._listeners: list[Callable[[], None]] = []
        self._event_listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._color_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._disconnected = asyncio.Event()
        self._colors_fps = 0

    # --- listeners -------------------------------------------------------------
    @callback
    def add_listener(self, cb: Callable[[], None]) -> CALLBACK_TYPE:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def add_event_listener(self, cb: Callable[[str, dict[str, Any]], None]) -> CALLBACK_TYPE:
        self._event_listeners.append(cb)
        return lambda: self._event_listeners.remove(cb)

    @callback
    def add_color_listener(self, cb: Callable[[dict[str, Any]], None]) -> CALLBACK_TYPE:
        self._color_listeners.append(cb)
        return lambda: self._color_listeners.remove(cb)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # --- lifecycle -------------------------------------------------------------
    async def async_connect_once(self) -> None:
        """First connection (during setup). Errors propagate to the caller."""
        await self._connect()

    def start(self) -> None:
        self._task = self.entry.async_create_background_task(
            self.hass, self._run(), f"holix {self.device.id}"
        )

    async def async_stop(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
        if self.conn is not None:
            await self.conn.close()

    async def _connect(self) -> None:
        data = self.entry.data
        conn = HolixConnection(
            async_get_clientsession(self.hass),
            data["host"],
            data["port"],
            data.get(CONF_PATH, "/holix/v1"),
        )
        conn.on_message = self._on_message
        conn.on_close = self._on_close
        self.device = await conn.connect()
        try:
            await conn.auth(data[CONF_CLIENT_ID], data[CONF_TOKEN])
        except HolixError:
            await conn.close()
            raise
        self.conn = conn
        self.connected = True
        self._disconnected.clear()
        if self._colors_fps:
            await conn.subscribe_colors(self._colors_fps)
        self._notify()

    async def _run(self) -> None:
        attempt = 0
        while not self._closed:
            if not self.connected:
                try:
                    await self._connect()
                    attempt = 0
                except HolixAuthError:
                    _LOGGER.warning("%s: authorization rejected, re-pairing required", self.device.name)
                    self.entry.async_start_reauth(self.hass)
                    return
                except HolixError as err:
                    delay = BACKOFF[min(attempt, len(BACKOFF) - 1)]
                    attempt += 1
                    _LOGGER.debug("%s: could not connect (%s), retrying in %s s", self.device.name, err, delay)
                    await asyncio.sleep(delay)
                    continue
            await self._disconnected.wait()
            if self.conn is not None and self.conn.close_code == CLOSE_REVOKED:
                self.entry.async_start_reauth(self.hass)
                return

    @callback
    def _on_close(self, code: int | None) -> None:
        if not self.connected:
            return
        self.connected = False
        self._disconnected.set()
        self._notify()

    # --- incoming messages -----------------------------------------------------
    @callback
    def _on_message(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "state" and isinstance(msg.get("state"), dict):
            self.state = msg["state"]
            self._notify()
        elif mtype == "event" and isinstance(msg.get("event"), str):
            event = msg["event"]
            data = msg.get("data") or {}
            self.hass.bus.async_fire(
                EVENT_HOLIX,
                {"device_id": self.device_registry_id, "holix_id": self.device.id, "type": event, **data},
            )
            for cb in list(self._event_listeners):
                cb(event, data)
        elif mtype == "colors" and isinstance(msg.get("zones"), dict):
            for cb in list(self._color_listeners):
                cb(msg["zones"])

    # --- commands --------------------------------------------------------------
    async def command(self, command: str, **args: Any) -> dict[str, Any]:
        if self.conn is None or not self.connected:
            from .api import HolixConnectionError  # noqa: PLC0415

            raise HolixConnectionError("device not connected")
        return await self.conn.command(command, **args)

    async def set_color_subscription(self, fps: int) -> None:
        """fps 0 = unsubscribe. Re-applied automatically on reconnect."""
        self._colors_fps = fps
        if self.conn is None or not self.connected:
            return
        if fps:
            await self.conn.subscribe_colors(fps)
        else:
            await self.conn.unsubscribe_colors()

    # --- convenience -----------------------------------------------------------
    @property
    def playback(self) -> dict[str, Any]:
        return self.state.get("playback") or {}

    @property
    def item(self) -> dict[str, Any] | None:
        return self.playback.get("item")

    def has(self, capability: str) -> bool:
        return capability in self.device.capabilities
