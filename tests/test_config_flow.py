"""Config flow tests."""

from __future__ import annotations

import asyncio
from ipaddress import ip_address

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.holix.const import (
    CONF_AMBIENT_FPS,
    CONF_AMBIENT_LIGHTS,
    CONF_DEVICE_ID,
    CONF_TOKEN,
    DOMAIN,
)

from .conftest import PIN
from .fake_device import FakeHolixDevice


def _zc(device: FakeHolixDevice, **props: str) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("127.0.0.1"),
        ip_addresses=[ip_address("127.0.0.1")],
        port=device.port,
        hostname="appletv.local.",
        type="_holixplay._tcp.local.",
        name=f"{device.name}._holixplay._tcp.local.",
        properties={"id": device.device_id, "proto": "1", "path": "/holix/v1",
                    "platform": "tvos", "model": "AppleTV14,1", "ver": "0.4.0", **props},
    )


async def test_user_flow_pairs(hass: HomeAssistant, device: FakeHolixDevice) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: device.port}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "pair"
    assert device.shown_pins == [PIN]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pin": "000000"})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": "invalid_pin"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pin": PIN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room Apple TV"
    assert result["result"].unique_id == device.device_id
    data = result["data"]
    assert data[CONF_DEVICE_ID] == device.device_id
    assert device.tokens[data["client_id"]] == data[CONF_TOKEN]
    await hass.async_block_till_done()


async def test_user_flow_cannot_connect(hass: HomeAssistant, socket_enabled) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: 1}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_too_many_attempts_restarts_pairing(hass: HomeAssistant, device: FakeHolixDevice) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: device.port}
    )
    for _ in range(4):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pin": "999999"})
        assert result["errors"] == {"base": "invalid_pin"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pin": "999999"})
    assert result["errors"] == {"base": "too_many_attempts"}
    assert len(device.shown_pins) == 2  # a new PIN was shown
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pin": PIN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_zeroconf_flow(hass: HomeAssistant, device: FakeHolixDevice) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zc(device)
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "zeroconf_confirm"
    assert result["description_placeholders"] == {"name": "Living Room Apple TV"}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"pin": PIN})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == device.port
    await hass.async_block_till_done()


async def test_zeroconf_updates_existing_host(hass: HomeAssistant, device: FakeHolixDevice) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=device.device_id,
        data={CONF_HOST: "192.168.1.50", CONF_PORT: 1, "path": "/holix/v1"},
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zc(device)
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "127.0.0.1"
    assert entry.data[CONF_PORT] == device.port


async def test_zeroconf_rejects_bad_discovery(hass: HomeAssistant, device: FakeHolixDevice) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zc(device, id="")
    )
    assert result["reason"] == "invalid_discovery"
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zc(device, proto="2")
    )
    assert result["reason"] == "unsupported_version"


async def test_reauth_after_revoke(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    assert await hass.config_entries.async_setup(paired_entry.entry_id)
    await hass.async_block_till_done()
    old_token = paired_entry.data[CONF_TOKEN]
    await device.revoke(paired_entry.data["client_id"])
    for _ in range(50):
        await asyncio.sleep(0.01)
        await hass.async_block_till_done()
        if hass.config_entries.flow.async_progress_by_handler(DOMAIN):
            break
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1 and flows[0]["context"]["source"] == "reauth"
    flow_id = flows[0]["flow_id"]
    result = await hass.config_entries.flow.async_configure(flow_id, {})
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(flow_id, {"pin": PIN})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert paired_entry.data[CONF_TOKEN] != old_token
    assert device.authed_count == 1


async def test_options_flow(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    assert await hass.config_entries.async_setup(paired_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(paired_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"lights": ["light.sol", "light.sag"], CONF_AMBIENT_FPS: 3}
    )
    assert result["step_id"] == "zones"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"light.sol": "left", "light.sag": "right"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert paired_entry.options == {
        CONF_AMBIENT_LIGHTS: {"light.sol": "left", "light.sag": "right"},
        CONF_AMBIENT_FPS: 3,
    }
    assert device.subscribed_fps() == [3]
