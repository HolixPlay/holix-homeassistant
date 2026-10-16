"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest

from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.helpers import instance_id

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.holix.const import (
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_PATH,
    CONF_TOKEN,
    DOMAIN,
)

from .fake_device import FakeHolixDevice

PIN = "482913"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def expected_lingering_timers() -> bool:
    """The aiohttp WebSocket heartbeat timer can linger after a test."""
    return True


@pytest.fixture
async def device(socket_enabled) -> AsyncGenerator[FakeHolixDevice]:
    dev = FakeHolixDevice(pin=PIN)
    await dev.start()
    yield dev
    await dev.stop()


@pytest.fixture
async def paired_entry(hass: HomeAssistant, device: FakeHolixDevice) -> MockConfigEntry:
    """Entry already paired with the device (token stored on the device)."""
    client_id = await instance_id.async_get(hass)
    device.tokens[client_id] = "dGVzdC10b2tlbi0wMDAwMDAwMDAwMDAwMDAwMDAwMDA"
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=device.name,
        unique_id=device.device_id,
        data={
            CONF_HOST: "127.0.0.1",
            CONF_PORT: device.port,
            CONF_PATH: "/holix/v1",
            CONF_CLIENT_ID: client_id,
            CONF_TOKEN: device.tokens[client_id],
            CONF_DEVICE_ID: device.device_id,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def wait_for(hass: HomeAssistant, cond, timeout: float = 2.0) -> None:
    """Wait for pushes arriving over the network (async_block_till_done does not wait for socket reads)."""
    import asyncio  # noqa: PLC0415

    for _ in range(int(timeout / 0.01)):
        await hass.async_block_till_done()
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met")
