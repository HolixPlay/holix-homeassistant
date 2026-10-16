"""Setup, entities, events, commands, reconnect, ambilight bridge."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components import automation
from homeassistant.components.device_automation import DeviceAutomationType
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import (
    async_get_device_automations,
    async_mock_service,
)

from custom_components.holix.const import (
    CONF_AMBIENT_FPS,
    CONF_AMBIENT_LIGHTS,
    DOMAIN,
    EVENT_HOLIX,
)
from custom_components.holix.ambient import to_light_params

from .conftest import wait_for
from .fake_device import FakeHolixDevice

MP = "media_player.living_room_apple_tv"


async def _setup(hass: HomeAssistant, entry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


async def test_entities_follow_state(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    await _setup(hass, paired_entry)
    assert hass.states.get(MP).state == "idle"
    assert hass.states.get("sensor.living_room_apple_tv_profile").state == "Alex"
    assert hass.states.get("sensor.living_room_apple_tv_media_type").state == "none"
    assert hass.states.get("select.living_room_apple_tv_profile").attributes["options"] == ["Alex", "Kids", "Guest"]
    assert hass.states.get("light.living_room_apple_tv_ambilight").state == "off"
    assert hass.states.get("binary_sensor.living_room_apple_tv_hdr").state == "off"

    await device.start_playback()
    await wait_for(hass, lambda: hass.states.get(MP).state == "playing")
    st = hass.states.get(MP)
    assert st.attributes["media_title"] == "Dune: Part Two"
    assert st.attributes["media_content_type"] == "movie"
    assert st.attributes["media_duration"] == 9960.0
    assert st.attributes["dynamic_range"] == "dolby_vision"
    assert hass.states.get("sensor.living_room_apple_tv_dynamic_range").state == "dolby_vision"
    assert hass.states.get("sensor.living_room_apple_tv_video_resolution").state == "2160p"
    assert hass.states.get("sensor.living_room_apple_tv_audio_format").state == "TRUEHD 7.1 Atmos/spatial"
    assert hass.states.get("binary_sensor.living_room_apple_tv_hdr").state == "on"

    ent = er.async_get(hass).async_get(MP)
    dev = dr.async_get(hass).async_get(ent.device_id)
    assert dev.manufacturer == "IORA" and dev.model_id == "AppleTV14,1"


async def test_events_fire(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    await _setup(hass, paired_entry)
    seen: list[dict] = []
    hass.bus.async_listen(EVENT_HOLIX, callback(lambda e: seen.append(e.data)))
    await device.start_playback()
    await wait_for(hass, lambda: len(seen) == 1)
    assert seen[0]["type"] == "playback_started"
    assert seen[0]["item"]["title"] == "Dune: Part Two"
    ev = hass.states.get("event.living_room_apple_tv_playback")
    assert ev.attributes["event_type"] == "playback_started"
    assert ev.attributes["item_type"] == "movie"
    assert ev.attributes["dynamic_range"] == "dolby_vision"

    await device.emit("segment_started", {"segment": "outro", "item": device.state["playback"]["item"]})
    await wait_for(hass, lambda: len(seen) == 2)
    assert hass.states.get("event.living_room_apple_tv_playback").attributes["segment"] == "outro"


async def test_device_trigger(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    await _setup(hass, paired_entry)
    dev_id = er.async_get(hass).async_get(MP).device_id
    triggers = await async_get_device_automations(hass, DeviceAutomationType.TRIGGER, dev_id)
    types = {t["type"] for t in triggers}
    assert {"playback_started", "playback_stopped", "segment_started"} <= types

    calls = async_mock_service(hass, "test", "automation")
    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {automation.DOMAIN: [{
            "trigger": {"platform": "device", "domain": DOMAIN, "device_id": dev_id, "type": "playback_stopped"},
            "action": {"service": "test.automation", "data_template": {"reason": "{{ trigger.event.data.reason }}"}},
        }]},
    )
    await device.start_playback()
    await device.stop_playback("ended")
    await wait_for(hass, lambda: len(calls) == 1)
    assert calls[0].data["reason"] == "ended"


async def test_commands(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    await _setup(hass, paired_entry)
    await device.start_playback()
    await wait_for(hass, lambda: hass.states.get(MP).state == "playing")

    await hass.services.async_call("media_player", "media_pause", {"entity_id": MP}, blocking=True)
    await wait_for(hass, lambda: hass.states.get(MP).state == "paused")
    await hass.services.async_call("media_player", "media_seek", {"entity_id": MP, "seek_position": 42}, blocking=True)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.living_room_apple_tv_profile", "option": "Kids"}, blocking=True
    )
    await wait_for(hass, lambda: hass.states.get("sensor.living_room_apple_tv_profile").state == "Kids")
    await hass.services.async_call(
        "light", "turn_on", {"entity_id": "light.living_room_apple_tv_ambilight", "brightness": 128}, blocking=True
    )
    await wait_for(hass, lambda: hass.states.get("light.living_room_apple_tv_ambilight").state == "on")
    assert hass.states.get("light.living_room_apple_tv_ambilight").attributes["brightness"] == 128
    await hass.services.async_call(
        DOMAIN, "show_notification", {"entity_id": MP, "message": "Someone is at the door", "title": "Doorbell"}, blocking=True
    )
    await hass.services.async_call(
        "remote", "send_command", {"entity_id": "remote.living_room_apple_tv_navigation", "command": ["up", "select"], "delay_secs": 0},
        blocking=True,
    )
    await hass.services.async_call(
        "media_player", "play_media",
        {"entity_id": MP, "media_content_id": "ffeeddccbbaa99887766554433221100", "media_content_type": "movie"},
        blocking=True,
    )
    await hass.services.async_call("media_player", "media_stop", {"entity_id": MP}, blocking=True)
    await wait_for(hass, lambda: hass.states.get(MP).state == "idle")

    names = [c for c, _ in device.commands]
    assert names == ["pause", "seek", "select_profile", "set_ambilight", "notify", "key", "key", "play_item", "stop"]
    assert device.commands[1][1] == {"position": 42}
    assert device.commands[3][1] == {"on": True, "brightness": 0.502}
    assert device.notifications == [{"message": "Someone is at the door", "title": "Doorbell"}]


async def test_command_error_surfaces(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    import pytest  # noqa: PLC0415
    from homeassistant.exceptions import HomeAssistantError  # noqa: PLC0415

    await _setup(hass, paired_entry)
    with pytest.raises(HomeAssistantError, match="not_available"):
        await hass.services.async_call("media_player", "media_play", {"entity_id": MP}, blocking=True)


async def test_disconnect_and_reconnect(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    await _setup(hass, paired_entry)
    with patch("custom_components.holix.client.BACKOFF", (0.05,)):
        await device.go_background()
        await wait_for(hass, lambda: hass.states.get(MP).state == "off")
        assert hass.states.get("sensor.living_room_apple_tv_profile").state == "unavailable"
        await wait_for(hass, lambda: hass.states.get(MP).state == "idle")
    assert device.authed_count == 1


async def test_setup_retry_when_offline(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    hass.config_entries.async_update_entry(paired_entry, data={**paired_entry.data, "port": 1})
    await hass.config_entries.async_setup(paired_entry.entry_id)
    await hass.async_block_till_done()
    assert paired_entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_auth_failed(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    device.tokens.clear()
    await hass.config_entries.async_setup(paired_entry.entry_id)
    await hass.async_block_till_done()
    assert paired_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert flows and flows[0]["context"]["source"] == "reauth"


async def test_unload(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    await _setup(hass, paired_entry)
    assert await hass.config_entries.async_unload(paired_entry.entry_id)
    await hass.async_block_till_done()
    await wait_for(hass, lambda: device.authed_count == 0)


def test_to_light_params() -> None:
    assert to_light_params([0, 0, 0]) == ((0, 0, 0), 0)
    assert to_light_params([128, 64, 0]) == ((255, 128, 0), 128)
    assert to_light_params([300, -5, 10]) == ((255, 0, 10), 255)


async def test_ambient_bridge(hass: HomeAssistant, device: FakeHolixDevice, paired_entry) -> None:
    hass.config_entries.async_update_entry(
        paired_entry, options={CONF_AMBIENT_LIGHTS: {"light.sol": "left", "light.tavan": "average"}, CONF_AMBIENT_FPS: 5}
    )
    await _setup(hass, paired_entry)
    calls: list[ServiceCall] = async_mock_service(hass, "light", "turn_on")
    assert device.subscribed_fps() == [5]
    await device.start_playback()
    zones = {"left": [200, 0, 0], "right": [0, 0, 0], "top": [0, 0, 0], "bottom": [0, 0, 0],
             "center": [0, 0, 0], "average": [10, 20, 40]}
    await device.push_colors(zones)
    await wait_for(hass, lambda: len(calls) == 2)
    by = {c.data["entity_id"]: c.data for c in calls}
    assert by["light.sol"]["rgb_color"] == (255, 0, 0)
    assert by["light.sol"]["brightness"] == 200
    assert by["light.sol"]["transition"] == 0.2
    assert by["light.tavan"]["rgb_color"] == (64, 128, 255)
    # rate limit: a second frame arriving immediately is skipped
    await device.push_colors({**zones, "left": [0, 200, 0]})
    await hass.async_block_till_done()
    assert len(calls) == 2
