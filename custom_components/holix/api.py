"""Holix Local Control Protocol (HLCP v1) client.

Independent of Home Assistant (aiohttp only); `tools/holix_probe.py` uses it too.
Contract: docs/protocol.md.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field
import hashlib
import hmac
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

PROTO_VERSION = 1
DEFAULT_PORT = 47861
DEFAULT_PATH = "/holix/v1"
REQUEST_TIMEOUT = 10.0
HEARTBEAT = 30.0

CLOSE_REVOKED = 4001
CLOSE_BACKGROUND = 4002


class HolixError(Exception):
    """Generic error."""


class HolixConnectionError(HolixError):
    """Could not connect / connection lost."""


class HolixProtocolError(HolixError):
    """The peer violated the protocol contract."""


class HolixCommandError(HolixError):
    """The device answered the request with an error."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


class HolixAuthError(HolixCommandError):
    """Authorization invalid (re-pairing required)."""


def compute_hmac(token: str, nonce: str) -> str:
    """auth.hmac = hex(HMAC-SHA256(key=utf8(token), msg=utf8(nonce)))."""
    return hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()


@dataclass(slots=True)
class DeviceInfo:
    """hello.device."""

    id: str
    name: str
    model: str
    platform: str
    app_version: str
    capabilities: list[str] = field(default_factory=list)

    @classmethod
    def from_hello(cls, hello: dict[str, Any]) -> DeviceInfo:
        dev = hello["device"]
        return cls(
            id=dev["id"],
            name=dev["name"],
            model=dev["model"],
            platform=dev["platform"],
            app_version=dev["app_version"],
            capabilities=list(hello.get("capabilities", [])),
        )


class HolixConnection:
    """A single WebSocket connection: hello → (pair) → auth → state/event/colors stream."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = DEFAULT_PORT,
        path: str = DEFAULT_PATH,
    ) -> None:
        self._session = session
        self._url = f"ws://{_fmt_host(host)}:{port}{path}"
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self.hello: dict[str, Any] = {}
        self.device: DeviceInfo | None = None
        self.on_message: Callable[[dict[str, Any]], None] | None = None
        self.on_close: Callable[[int | None], None] | None = None
        self.close_code: int | None = None

    @property
    def connected(self) -> bool:
        return self._ws is not None and not self._ws.closed

    async def connect(self) -> DeviceInfo:
        """Connect and wait for hello."""
        try:
            self._ws = await self._session.ws_connect(
                self._url, heartbeat=HEARTBEAT, timeout=aiohttp.ClientWSTimeout(ws_close=5)
            )
            msg = await self._ws.receive_json(timeout=REQUEST_TIMEOUT)
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            await self.close()
            raise HolixConnectionError(str(err)) from err
        except (TypeError, ValueError) as err:
            await self.close()
            raise HolixProtocolError("invalid frame while waiting for hello") from err
        if not isinstance(msg, dict) or msg.get("type") != "hello":
            await self.close()
            raise HolixProtocolError("first message is not hello")
        if int(msg.get("proto", 0)) > PROTO_VERSION:
            await self.close()
            raise HolixProtocolError(f"unsupported proto {msg.get('proto')}")
        try:
            self.device = DeviceInfo.from_hello(msg)
        except (KeyError, TypeError) as err:
            await self.close()
            raise HolixProtocolError("hello.device eksik") from err
        self.hello = msg
        self._reader = asyncio.get_running_loop().create_task(self._read_loop())
        return self.device

    async def close(self) -> None:
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._reader is not None and self._reader is not asyncio.current_task():
            self._reader.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader
        self._reader = None
        self._fail_pending(HolixConnectionError("connection closed"))

    async def _read_loop(self) -> None:
        assert self._ws is not None
        ws = self._ws
        try:
            async for raw in ws:
                if raw.type != aiohttp.WSMsgType.TEXT:
                    continue
                try:
                    msg = raw.json()
                except ValueError:
                    _LOGGER.debug("invalid JSON: %s", raw.data[:200])
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") in ("result", "pong") and "id" in msg:
                    fut = self._pending.pop(msg["id"], None)
                    if fut is not None and not fut.done():
                        fut.set_result(msg)
                    continue
                if self.on_message is not None:
                    try:
                        self.on_message(msg)
                    except Exception:
                        _LOGGER.exception("failed to handle message: %s", msg.get("type"))
        finally:
            self.close_code = ws.close_code
            self._fail_pending(HolixConnectionError("connection closed"))
            if self.on_close is not None:
                self.on_close(self.close_code)

    def _fail_pending(self, err: Exception) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(err)
        self._pending.clear()

    async def request(self, msg_type: str, **fields: Any) -> dict[str, Any]:
        """Send a request; return `result.result` or raise HolixCommandError."""
        if not self.connected:
            raise HolixConnectionError("not connected")
        assert self._ws is not None
        self._next_id += 1
        msg_id = self._next_id
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self._ws.send_json({"type": msg_type, "id": msg_id, **fields})
            reply = await asyncio.wait_for(fut, REQUEST_TIMEOUT)
        except TimeoutError as err:
            raise HolixConnectionError(f"{msg_type} timed out") from err
        except (aiohttp.ClientError, ConnectionError) as err:
            raise HolixConnectionError(str(err)) from err
        finally:
            self._pending.pop(msg_id, None)
        if reply.get("type") == "pong":
            return {}
        if reply.get("success"):
            return reply.get("result") or {}
        error = reply.get("error") or {}
        code = error.get("code", "internal")
        if code in ("invalid_auth", "unauthorized"):
            raise HolixAuthError(code, error.get("message", ""))
        raise HolixCommandError(code, error.get("message", ""))

    async def pair_start(self, client_id: str, client_name: str) -> dict[str, Any]:
        return await self.request("pair_start", client_id=client_id, client_name=client_name)

    async def pair_finish(self, client_id: str, pin: str) -> str:
        result = await self.request("pair_finish", client_id=client_id, pin=pin)
        token = result.get("token")
        if not isinstance(token, str) or not token:
            raise HolixProtocolError("pair_finish returned no token")
        return token

    async def auth(self, client_id: str, token: str) -> None:
        nonce = self.hello.get("nonce")
        if not isinstance(nonce, str):
            raise HolixProtocolError("hello.nonce missing")
        await self.request("auth", client_id=client_id, hmac=compute_hmac(token, nonce))

    async def command(self, command: str, **args: Any) -> dict[str, Any]:
        return await self.request("command", command=command, args=args)

    async def subscribe_colors(self, fps: int) -> int:
        result = await self.request("subscribe_colors", fps=fps)
        return int(result.get("fps", fps))

    async def unsubscribe_colors(self) -> None:
        await self.request("unsubscribe_colors")

    async def ping(self) -> None:
        await self.request("ping")


def _fmt_host(host: str) -> str:
    return f"[{host}]" if ":" in host and not host.startswith("[") else host
