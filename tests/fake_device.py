"""HLCP v1 reference device implementation (fake Holix Play).

Used by the tests; when the spec is ambiguous, its behavior is the reference (docs/protocol.md §10).
Run by hand (to try against a real HA):
    python -I tests/fake_device.py --port 47861 --pin 123456
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import hmac
import secrets
import time
from typing import Any

from aiohttp import WSCloseCode, web

PATH = "/holix/v1"

PROFILES = [
    {"id": "p-alex", "name": "Alex", "kind": "adult"},
    {"id": "p-kids", "name": "Kids", "kind": "child"},
    {"id": "p-guest", "name": "Guest", "kind": "guest"},
]

SAMPLE_ITEM = {
    "id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "type": "movie",
    "title": "Dune: Part Two",
    "series_title": None,
    "season": None,
    "episode": None,
    "year": 2024,
    "official_rating": "PG-13",
    "image_url": "https://jf.example.net/Items/a1b2c3d4e5f60718293a4b5c6d7e8f90/Images/Primary?tag=abc",
}
SAMPLE_VIDEO = {"dynamic_range": "dolby_vision", "width": 3840, "height": 2160, "codec": "hevc", "frame_rate": 23.976}
SAMPLE_AUDIO = {"codec": "truehd", "channels": 8, "spatial": True, "language": "eng"}

KEYS = {"up", "down", "left", "right", "select", "back", "home", "menu", "play_pause"}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def idle_state() -> dict[str, Any]:
    return {
        "playback": {
            "state": "idle", "position": None, "duration": None, "position_at": None, "rate": 1.0,
            "item": None, "video": None, "audio": None, "subtitle": None, "segment": None,
            "play_method": None, "volume": None, "muted": False,
        },
        "profile": copy.deepcopy(PROFILES[0]),
        "profiles": copy.deepcopy(PROFILES),
        "ambilight": {"on": False, "brightness": 0.8, "targets": 1},
        "screensaver": False,
    }


def now_ms() -> int:
    return int(time.time() * 1000)


class Conn:
    def __init__(self, ws: web.WebSocketResponse, nonce: str) -> None:
        self.ws = ws
        self.nonce = nonce
        self.client_id: str | None = None
        self.authed = False
        self.colors_fps = 0


class FakeHolixDevice:
    def __init__(
        self,
        *,
        device_id: str = "5b0c1d9e-3f0a-4c55-9a43-1f0e2b7a9c10",
        name: str = "Living Room Apple TV",
        platform: str = "tvos",
        model: str = "AppleTV14,1",
        capabilities: list[str] | None = None,
        pin: str | None = None,
    ) -> None:
        self.device_id = device_id
        self.name = name
        self.platform = platform
        self.model = model
        self.capabilities = capabilities if capabilities is not None else [
            "playback", "profiles", "ambilight", "colors", "notify", "keys", "play_item",
        ]
        self.fixed_pin = pin
        self.state = idle_state()
        self.tokens: dict[str, str] = {}
        self.pairing: dict[str, Any] | None = None
        self.conns: list[Conn] = []
        self.sent: list[dict[str, Any]] = []
        self.received: list[dict[str, Any]] = []
        self.commands: list[tuple[str, dict[str, Any]]] = []
        self.notifications: list[dict[str, Any]] = []
        self.shown_pins: list[str] = []
        self.colors_seq = 0
        self.runner: web.AppRunner | None = None
        self.port = 0

    # --- server ----------------------------------------------------------------
    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(PATH, self._handle)
        return app

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        self.runner = web.AppRunner(self.app())
        await self.runner.setup()
        site = web.TCPSite(self.runner, host, port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        return self.port

    async def stop(self) -> None:
        for c in list(self.conns):
            await c.ws.close(code=4002, message=b"background")
        if self.runner is not None:
            await self.runner.cleanup()

    async def _send(self, c: Conn, msg: dict[str, Any]) -> None:
        self.sent.append(msg)
        if not c.ws.closed:
            await c.ws.send_json(msg)

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        c = Conn(ws, b64url(secrets.token_bytes(16)))
        self.conns.append(c)
        await self._send(c, {
            "type": "hello", "proto": 1,
            "device": {"id": self.device_id, "name": self.name, "model": self.model,
                       "platform": self.platform, "app_version": "0.4.0 (57)"},
            "nonce": c.nonce, "capabilities": self.capabilities,
        })
        try:
            async for raw in ws:
                if raw.type != web.WSMsgType.TEXT:
                    continue
                try:
                    msg = raw.json()
                except ValueError:
                    await ws.close(code=4000, message=b"bad json")
                    break
                self.received.append(msg)
                await self._dispatch(c, msg)
        finally:
            self.conns.remove(c)
        return ws

    async def _ok(self, c: Conn, mid: Any, result: dict[str, Any] | None = None) -> None:
        await self._send(c, {"type": "result", "id": mid, "success": True, "result": result or {}})

    async def _err(self, c: Conn, mid: Any, code: str, message: str = "") -> None:
        await self._send(c, {"type": "result", "id": mid, "success": False,
                             "error": {"code": code, "message": message}})

    async def _dispatch(self, c: Conn, msg: dict[str, Any]) -> None:
        t, mid = msg.get("type"), msg.get("id")
        if t == "ping":
            await self._send(c, {"type": "pong", "id": mid})
            return
        if t == "pair_start":
            pin = self.fixed_pin or f"{secrets.randbelow(10**6):06d}"
            self.pairing = {"client_id": msg.get("client_id"), "pin": pin,
                            "expires": time.monotonic() + 120, "attempts": 0}
            self.shown_pins.append(pin)
            await self._ok(c, mid, {"pin_length": 6, "expires_in": 120})
            return
        if t == "pair_finish":
            p = self.pairing
            if not p or p["client_id"] != msg.get("client_id") or time.monotonic() > p["expires"]:
                await self._err(c, mid, "pairing_not_started")
                return
            if not hmac.compare_digest(str(msg.get("pin", "")), p["pin"]):
                p["attempts"] += 1
                if p["attempts"] >= 5:
                    self.pairing = None
                    await self._err(c, mid, "too_many_attempts")
                else:
                    await self._err(c, mid, "invalid_pin", f"{5 - p['attempts']} attempts left")
                return
            self.pairing = None
            token = b64url(secrets.token_bytes(32))
            self.tokens[p["client_id"]] = token
            await self._ok(c, mid, {"token": token})
            return
        if t == "auth":
            token = self.tokens.get(msg.get("client_id", ""))
            expected = hmac.new(token.encode(), c.nonce.encode(), hashlib.sha256).hexdigest() if token else ""
            if not token or not hmac.compare_digest(expected, str(msg.get("hmac", ""))):
                await self._err(c, mid, "invalid_auth")
                return
            c.authed, c.client_id = True, msg["client_id"]
            await self._ok(c, mid)
            await self._send(c, {"type": "state", "state": self.state})
            return
        if t not in ("command", "subscribe_colors", "unsubscribe_colors"):
            if mid is not None:
                await self._err(c, mid, "unknown_type")
            return
        if not c.authed:
            await self._err(c, mid, "unauthorized")
            return
        if t == "subscribe_colors":
            fps = int(msg.get("fps", 0))
            if not 1 <= fps <= 30:
                await self._err(c, mid, "invalid_args", "fps 1–30")
                return
            c.colors_fps = fps
            await self._ok(c, mid, {"fps": fps})
            return
        if t == "unsubscribe_colors":
            c.colors_fps = 0
            await self._ok(c, mid)
            return
        await self._command(c, mid, msg.get("command"), msg.get("args") or {})

    async def _command(self, c: Conn, mid: Any, cmd: Any, args: dict[str, Any]) -> None:
        pb = self.state["playback"]
        self.commands.append((cmd, args))
        if cmd in ("play", "pause", "play_pause", "stop", "next", "previous", "seek") and pb["item"] is None:
            await self._err(c, mid, "not_available", "nothing is playing")
            return
        if cmd == "play" or (cmd == "play_pause" and pb["state"] == "paused"):
            await self._ok(c, mid)
            await self.set_playback(state="playing", event="playback_resumed")
        elif cmd == "pause" or cmd == "play_pause":
            await self._ok(c, mid)
            await self.set_playback(state="paused", event="playback_paused")
        elif cmd == "stop":
            await self._ok(c, mid)
            await self.stop_playback("user")
        elif cmd in ("next", "previous"):
            await self._ok(c, mid)
        elif cmd == "seek":
            pos = args.get("position")
            if not isinstance(pos, (int, float)) or pos < 0:
                await self._err(c, mid, "invalid_args", "position")
                return
            await self._ok(c, mid)
            await self.set_playback(position=float(pos))
        elif cmd in ("set_volume", "mute"):
            if "volume" not in self.capabilities:
                await self._err(c, mid, "not_supported")
                return
            await self._ok(c, mid)
            if cmd == "set_volume":
                pb["volume"] = float(args["level"])
            else:
                pb["muted"] = bool(args["muted"])
            await self.broadcast_state()
        elif cmd == "select_profile":
            prof = next((p for p in self.state["profiles"] if p["id"] == args.get("profile_id")), None)
            if prof is None:
                await self._err(c, mid, "invalid_args", "unknown profile")
                return
            await self._ok(c, mid)
            self.state["profile"] = copy.deepcopy(prof)
            await self.broadcast_state()
            await self.emit("profile_changed", {"profile": prof})
        elif cmd == "set_ambilight":
            if "ambilight" not in self.capabilities or self.state["ambilight"] is None:
                await self._err(c, mid, "not_supported")
                return
            if "on" not in args and "brightness" not in args:
                await self._err(c, mid, "invalid_args")
                return
            await self._ok(c, mid)
            if "on" in args:
                self.state["ambilight"]["on"] = bool(args["on"])
            if "brightness" in args:
                self.state["ambilight"]["brightness"] = float(args["brightness"])
            await self.broadcast_state()
        elif cmd == "play_item":
            if not isinstance(args.get("item_id"), str):
                await self._err(c, mid, "invalid_args")
                return
            await self._ok(c, mid)
            await self.start_playback({**SAMPLE_ITEM, "id": args["item_id"]})
        elif cmd == "notify":
            if not isinstance(args.get("message"), str):
                await self._err(c, mid, "invalid_args")
                return
            self.notifications.append(args)
            await self._ok(c, mid)
        elif cmd == "key":
            if args.get("key") not in KEYS:
                await self._err(c, mid, "invalid_args")
                return
            await self._ok(c, mid)
        else:
            await self._err(c, mid, "unknown_command")

    # --- device-side triggers (called by tests) ---------------------------------
    async def broadcast(self, msg: dict[str, Any]) -> None:
        for c in list(self.conns):
            if c.authed:
                await self._send(c, msg)

    async def broadcast_state(self) -> None:
        await self.broadcast({"type": "state", "state": self.state})

    async def emit(self, event: str, data: dict[str, Any]) -> None:
        await self.broadcast({"type": "event", "event": event, "ts": now_ms(), "data": data})

    async def set_playback(self, *, event: str | None = None, **fields: Any) -> None:
        pb = self.state["playback"]
        pb.update(fields)
        pb["position_at"] = now_ms()
        await self.broadcast_state()
        if event:
            await self.emit(event, {"item": pb["item"], "position": pb["position"]})

    async def start_playback(self, item: dict[str, Any] | None = None) -> None:
        pb = self.state["playback"]
        pb.update(state="playing", position=0.0, duration=9960.0, position_at=now_ms(), rate=1.0,
                  item=copy.deepcopy(item or SAMPLE_ITEM), video=dict(SAMPLE_VIDEO), audio=dict(SAMPLE_AUDIO),
                  subtitle={"language": "tur", "forced": False}, play_method="direct_play")
        await self.broadcast_state()
        await self.emit("playback_started", {"item": pb["item"], "video": pb["video"],
                                             "audio": pb["audio"], "profile": self.state["profile"]})

    async def stop_playback(self, reason: str = "ended") -> None:
        pb = self.state["playback"]
        item, pos, dur = pb["item"], pb["position"], pb["duration"]
        self.state["playback"] = idle_state()["playback"]
        await self.broadcast_state()
        await self.emit("playback_stopped", {"item": item, "position": pos, "duration": dur, "reason": reason})

    async def push_colors(self, zones: dict[str, list[int]]) -> None:
        self.colors_seq += 1
        for c in list(self.conns):
            if c.authed and c.colors_fps:
                await self._send(c, {"type": "colors", "seq": self.colors_seq, "ts": now_ms(), "zones": zones})

    async def revoke(self, client_id: str) -> None:
        self.tokens.pop(client_id, None)
        for c in list(self.conns):
            if c.client_id == client_id:
                await c.ws.close(code=4001, message=b"revoked")

    async def go_background(self) -> None:
        for c in list(self.conns):
            await c.ws.close(code=4002, message=b"background")

    @property
    def authed_count(self) -> int:
        return sum(1 for c in self.conns if c.authed)

    def subscribed_fps(self) -> list[int]:
        return [c.colors_fps for c in self.conns if c.authed]


async def _main() -> None:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=47861)
    ap.add_argument("--pin")
    a = ap.parse_args()
    dev = FakeHolixDevice(pin=a.pin)
    await dev.start(a.host, a.port)
    print(f"fake Holix: ws://{a.host}:{dev.port}{PATH}  (PINs are printed to stdout)")
    shown = 0
    while True:
        await asyncio.sleep(0.5)
        while shown < len(dev.shown_pins):
            print("PIN:", dev.shown_pins[shown])
            shown += 1


if __name__ == "__main__":
    asyncio.run(_main())
