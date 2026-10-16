"""Contract tests: HMAC vector, schema, sample messages, reference device output."""

from __future__ import annotations

import json
from pathlib import Path

import aiohttp
import jsonschema
import pytest

from custom_components.holix.api import (
    HolixAuthError,
    HolixCommandError,
    HolixConnection,
    compute_hmac,
)

from .fake_device import FakeHolixDevice

ROOT = Path(__file__).parent.parent
SCHEMA = json.loads((ROOT / "docs" / "protocol.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
FIXTURES = sorted((Path(__file__).parent / "fixtures").glob("*.json"))


def test_hmac_vector() -> None:
    assert (
        compute_hmac("dGVzdC10b2tlbi0wMDAwMDAwMDAwMDAwMDAwMDAwMDA", "bm9uY2UtMDAwMDAwMDAwMA")
        == "c607ad23048f0e5684d11cd65c51c128405a1e20d6615ce89af5b1cc2cea5c7d"
    )


def test_schema_is_valid() -> None:
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("path", FIXTURES, ids=[p.stem for p in FIXTURES])
def test_fixture_matches_schema(path: Path) -> None:
    VALIDATOR.validate(json.loads(path.read_text()))


@pytest.mark.parametrize(
    "msg",
    [
        {"type": "hello", "proto": 1, "device": {"id": "x"}, "nonce": "short", "capabilities": []},
        {"type": "auth", "id": 1, "client_id": "c", "hmac": "XYZ"},
        {"type": "pair_finish", "id": 1, "client_id": "c", "pin": "12345"},
        {"type": "command", "id": 1, "command": "seek", "args": {}},
        {"type": "command", "id": 1, "command": "key", "args": {"key": "jump"}},
        {"type": "result", "id": 1, "success": False},
        {"type": "result", "id": 1, "success": False, "error": {"code": "nope"}},
        {"type": "colors", "seq": 1, "ts": 0, "zones": {"left": [0, 0, 256]}},
        {"type": "event", "event": "playback_stopped", "ts": 0, "data": {"reason": "bored"}},
        {"type": "mystery"},
    ],
)
def test_schema_rejects(msg: dict) -> None:
    assert not VALIDATOR.is_valid(msg)


@pytest.mark.parametrize("expected_lingering_timers", [True])
async def test_full_session_conforms(expected_lingering_timers: bool, socket_enabled) -> None:
    """Every message the reference device sends/receives during a session conforms to the schema."""
    dev = FakeHolixDevice(pin="111222")
    await dev.start()
    async with aiohttp.ClientSession() as session:
        conn = HolixConnection(session, "127.0.0.1", dev.port)
        got: list[dict] = []
        conn.on_message = got.append
        info = await conn.connect()
        assert info.platform == "tvos"
        with pytest.raises(HolixAuthError):
            await conn.auth("cid", "wrong")
        with pytest.raises(HolixAuthError):
            await conn.command("play")
        await conn.pair_start("cid", "Test")
        with pytest.raises(HolixCommandError) as err:
            await conn.pair_finish("cid", "000000")
        assert err.value.code == "invalid_pin"
        token = await conn.pair_finish("cid", "111222")
        await conn.auth("cid", token)
        with pytest.raises(HolixCommandError) as err:
            await conn.command("play")
        assert err.value.code == "not_available"
        assert await conn.subscribe_colors(4) == 4
        await dev.start_playback()
        await conn.command("seek", position=120.5)
        await conn.command("pause")
        await conn.command("select_profile", profile_id="p-kids")
        await conn.command("set_ambilight", on=True, brightness=0.4)
        await conn.command("notify", message="Merhaba")
        await conn.command("key", key="up")
        with pytest.raises(HolixCommandError) as err:
            await conn.command("set_volume", level=0.5)
        assert err.value.code == "not_supported"
        with pytest.raises(HolixCommandError) as err:
            await conn.command("warp")
        assert err.value.code == "unknown_command"
        await dev.push_colors({z: [1, 2, 3] for z in ("left", "right", "top", "bottom", "center", "average")})
        await dev.emit("segment_started", {"segment": "outro", "item": dev.state["playback"]["item"]})
        await dev.stop_playback("user")
        await conn.ping()
        await conn.close()
    await dev.stop()
    msgs = dev.sent + dev.received
    for m in msgs:
        errors = list(VALIDATOR.iter_errors(m))
        assert not errors, (m, [e.message for e in errors])
    types = {m["type"] for m in dev.sent}
    assert {"hello", "result", "state", "event", "colors", "pong"} <= types
    assert dev.state["profile"]["id"] == "p-kids"
    assert dev.state["ambilight"] == {"on": True, "brightness": 0.4, "targets": 1}
    assert any(m["type"] == "colors" for m in got)
