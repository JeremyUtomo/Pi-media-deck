"""Interactive test client for the pc-companion WebSocket protocol (docs/CONTEXT.md §6).

Usage:
    pip install websockets
    python tools/deck_client.py [ws://host:8765]

Prints every message from the PC (base64 payloads shortened) and sends commands typed at the prompt:
    play | next | prev              media.command
    vol <target> <0-100>            audio.set_volume (target: master or a session id like spotify.exe)
    mute|unmute <target>            audio.set_mute
    ping
    raw <json>                      send any JSON message
    quit
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

DEFAULT_URL = "ws://localhost:8765"
CLIENT_NAME = "deck-client/0.1"
MEDIA_ACTIONS = {"play": "play_pause", "next": "next", "prev": "previous"}


def describe(message: dict) -> str:
    match message.get("type"):
        case "media.art" | "audio.icon":
            size = len(message.get("data", "")) * 3 // 4
            return f"{message['type']} hash={message.get('hash')} {message.get('mime')} {size / 1024:.1f} KB"
        case "media.state" if message.get("available"):
            state = "playing" if message["isPlaying"] else "paused"
            return (
                f"media.state {state}: {message['title']} — {message['artist']} ({message['album']}) "
                f"{message['positionMs'] // 1000}s/{message['durationMs'] // 1000}s art={message['artHash']}"
            )
        case "audio.state":
            master = message["master"]
            strips = [f"master {master['volume']:.0%}{' M' if master['muted'] else ''}"]
            strips += [
                f"{s['id']} {s['volume']:.0%}{' M' if s['muted'] else ''}{'' if s.get('iconHash') else ' (no icon)'}"
                for s in message["sessions"]
            ]
            current = next((o["name"] for o in message.get("outputs", []) if o["id"] == message.get("output")), "?")
            return f"audio.state [output: {current}] " + " | ".join(strips)
        case _:
            return json.dumps(message, ensure_ascii=False)


def parse_command(line: str) -> dict | None:
    parts = line.split()
    if not parts:
        return None
    cmd, args = parts[0].lower(), parts[1:]

    if cmd in MEDIA_ACTIONS:
        return {"type": "media.command", "action": MEDIA_ACTIONS[cmd]}
    if cmd == "vol" and len(args) == 2:
        return {"type": "audio.set_volume", "target": args[0], "volume": float(args[1]) / 100}
    if cmd in ("mute", "unmute") and len(args) == 1:
        return {"type": "audio.set_mute", "target": args[0], "muted": cmd == "mute"}
    if cmd == "ping":
        return {"type": "ping"}
    if cmd == "raw" and args:
        return json.loads(line.split(maxsplit=1)[1])
    raise ValueError("unknown command")


async def receive(ws: websockets.ClientConnection) -> None:
    async for raw in ws:
        print(f"< {describe(json.loads(raw))}")


async def send_commands(ws: websockets.ClientConnection) -> None:
    loop = asyncio.get_running_loop()
    while True:
        line = await loop.run_in_executor(None, sys.stdin.readline)
        if not line or line.strip().lower() in ("quit", "exit", "q"):
            return
        try:
            message = parse_command(line)
        except (ValueError, json.JSONDecodeError) as error:
            print(f"! {error}")
            continue
        if message is not None:
            await ws.send(json.dumps(message))
            print(f"> {json.dumps(message)}")


async def main(url: str) -> None:
    async with websockets.connect(url, max_size=8 * 1024 * 1024) as ws:
        print(f"connected to {url}")
        await ws.send(json.dumps({"type": "hello", "protocol": 1, "client": CLIENT_NAME}))
        receiver = asyncio.create_task(receive(ws))
        await send_commands(ws)
        receiver.cancel()


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL))
    except KeyboardInterrupt:
        pass
