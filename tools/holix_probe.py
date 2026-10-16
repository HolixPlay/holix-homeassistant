#!/usr/bin/env python3
"""HLCP v1 conformance check: connects to a real Holix Play app, pairs, and validates every message against the schema.

Usage (with the project .venv; requires aiohttp, jsonschema):
    .venv/bin/python tools/holix_probe.py 192.168.1.50 [--port 47861] [--token-file /tmp/holix.token]
        [--commands] [--colors 4] [--listen 60]

- If the token file is missing, starts pairing; asks for the PIN and writes the token to the file.
- --commands: tries safe commands (notify, key up, set_ambilight with the same value, seek only while playing).
- --listen N: validates and prints state/event/colors messages for N seconds.
Exit code: 0 = all messages conform to the schema, 1 = violation, 2 = connection/authorization error.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
import uuid

import aiohttp
import jsonschema

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("holix_api", ROOT / "custom_components/holix/api.py")
api = importlib.util.module_from_spec(_spec)
sys.modules["holix_api"] = api
_spec.loader.exec_module(api)  # type: ignore[union-attr]

SCHEMA = json.loads((ROOT / "docs/protocol.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
violations = 0


def check(msg: dict, direction: str) -> None:
    global violations
    errs = list(VALIDATOR.iter_errors(msg))
    tag = "OK " if not errs else "FAIL"
    print(f"{tag} {direction} {msg.get('type')}{' ' + msg.get('event', '') if msg.get('type') == 'event' else ''}")
    for e in errs:
        violations += 1
        print(f"     {list(e.absolute_path)}: {e.message}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=api.DEFAULT_PORT)
    ap.add_argument("--path", default=api.DEFAULT_PATH)
    ap.add_argument("--token-file", default=str(Path.home() / ".holix-probe.json"))
    ap.add_argument("--commands", action="store_true")
    ap.add_argument("--colors", type=int, default=0)
    ap.add_argument("--listen", type=float, default=10)
    a = ap.parse_args()

    tf = Path(a.token_file)
    saved = json.loads(tf.read_text()) if tf.exists() else {}
    client_id = saved.get("client_id") or uuid.uuid4().hex

    async with aiohttp.ClientSession() as session:
        conn = api.HolixConnection(session, a.host, a.port, a.path)
        conn.on_message = lambda m: check(m, "←")
        try:
            dev = await conn.connect()
        except api.HolixError as err:
            print("could not connect:", err)
            return 2
        check(conn.hello, "←")
        print(f"device: {dev.name} ({dev.platform}, {dev.model}, {dev.app_version}) capabilities={dev.capabilities}")

        token = saved.get("tokens", {}).get(dev.id)
        if not token:
            await conn.pair_start(client_id, "holix_probe")
            pin = input("Ekrandaki PIN: ").strip()
            try:
                token = await conn.pair_finish(client_id, pin)
            except api.HolixCommandError as err:
                print("pairing error:", err.code)
                return 2
            saved.setdefault("tokens", {})[dev.id] = token
            saved["client_id"] = client_id
            tf.write_text(json.dumps(saved))
            tf.chmod(0o600)
        try:
            await conn.auth(client_id, token)
        except api.HolixAuthError:
            print("authorization rejected; remove this device from the token file and pair again")
            return 2
        await asyncio.sleep(0.5)

        async def try_cmd(name: str, **args) -> None:
            check({"type": "command", "id": 1, "command": name, "args": args}, "→")
            try:
                await conn.command(name, **args)
                print(f"   {name}: ok")
            except api.HolixCommandError as err:
                print(f"   {name}: error {err.code}")

        if a.commands:
            if "notify" in dev.capabilities:
                await try_cmd("notify", message="holix_probe connection test", duration=3)
            if "keys" in dev.capabilities:
                await try_cmd("key", key="up")
            await try_cmd("warp")  # expect unknown_command
        if a.colors:
            print("color stream fps =", await conn.subscribe_colors(a.colors))
        await conn.ping()
        await asyncio.sleep(a.listen)
        await conn.close()
    print(f"\nviolations: {violations}")
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
