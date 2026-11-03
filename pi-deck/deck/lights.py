"""IKEA DIRIGERA client for one room's lights. Runs on its own thread; talks to the UI only through the inbox.

Talks to the hub directly over the LAN (not via the PC): REST (GET /v1/devices, PATCH /v1/devices/{id}) and the
hub's event WebSocket (wss://hub:8443/v1) for live state. Credentials come from ~/.config/pi-deck/dirigera.json,
written by scripts/dirigera_pair.py. The hub's certificate is self-signed, so its SHA-256 is pinned instead.
"""

from __future__ import annotations

import asyncio
import hashlib
import http.client
import json
import logging
import queue
import ssl
import threading
from collections.abc import Callable
from pathlib import Path

import websockets

log = logging.getLogger(__name__)

DIRIGERA_CONFIG = Path.home() / ".config" / "pi-deck" / "dirigera.json"
PORT = 8443
BACKOFF_START_S = 2.0
BACKOFF_MAX_S = 30.0

# Messages posted to the inbox.
LIGHTS_STATE = "lights.state"  # {"lamps": [lamp, ...]}
LIGHTS_UNAVAILABLE = "lights.unavailable"


def parse_lamp(device: dict) -> dict:
    """The parts of a DIRIGERA light the deck uses."""
    a = device.get("attributes", {})
    can = device.get("capabilities", {}).get("canReceive", [])
    # The hub's colorTemperatureMin/Max are swapped relative to Kelvin (min holds the coolest value).
    temps = [t for t in (a.get("colorTemperatureMin"), a.get("colorTemperatureMax")) if t]
    return {
        "id": device["id"],
        "name": a.get("customName") or "Lamp",
        "reachable": bool(device.get("isReachable", True)),
        "on": bool(a.get("isOn")),
        "level": int(a.get("lightLevel") or 0),
        "temperature": a.get("colorTemperature"),
        "warmest": min(temps) if temps else None,
        "coolest": max(temps) if temps else None,
        "can_temperature": "colorTemperature" in can,
        "can_color": "colorHue" in can,
    }


# Event attribute names -> lamp keys.
_ATTRIBUTES = {"isOn": "on", "lightLevel": "level", "colorTemperature": "temperature"}


class LightsClient:
    """Keeps the lamps of one DIRIGERA room in sync and sends commands for them."""

    def __init__(self, room: str, inbox: queue.Queue[dict], wake: Callable[[], None], config: Path = DIRIGERA_CONFIG) -> None:
        self._room = room.casefold()
        self._inbox = inbox
        self._wake = wake
        self._settings = json.loads(config.read_text()) if config.exists() else None
        self._lamps: dict[str, dict] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._commands: asyncio.Queue[tuple[str, dict]] | None = None
        self._pool: queue.SimpleQueue[http.client.HTTPSConnection] = queue.SimpleQueue()  # idle keep-alive connections
        self._thread = threading.Thread(target=lambda: asyncio.run(self._main()), name="lights", daemon=True)

    @property
    def configured(self) -> bool:
        return self._settings is not None

    def start(self) -> None:
        if self.configured:
            self._thread.start()
        else:
            log.warning("lights: %s missing; run scripts/dirigera_pair.py", DIRIGERA_CONFIG)

    def set(self, lamp_ids: list[str], **attributes) -> None:
        """Sends attributes (DIRIGERA names: isOn, lightLevel, colorTemperature) to each lamp. Thread-safe."""
        loop, commands = self._loop, self._commands
        if loop is None or commands is None:
            return
        for lamp_id in lamp_ids:
            loop.call_soon_threadsafe(commands.put_nowait, (lamp_id, attributes))

    # --- network thread -------------------------------------------------------------------------------------

    def _context(self) -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # self-signed; pinned by fingerprint in _check_certificate
        return context

    def _check_certificate(self, der: bytes) -> None:
        assert self._settings is not None
        if hashlib.sha256(der).hexdigest() != self._settings["cert_sha256"]:
            raise ConnectionError("DIRIGERA certificate changed; re-run scripts/dirigera_pair.py")

    def _connect(self) -> http.client.HTTPSConnection:
        """A new HTTPS connection to the hub; the certificate is checked before any token is sent."""
        assert self._settings is not None
        connection = http.client.HTTPSConnection(self._settings["host"], PORT, context=self._context(), timeout=10)
        connection.connect()
        self._check_certificate(connection.sock.getpeercert(binary_form=True))  # type: ignore[union-attr]
        return connection

    def _request(self, method: str, path: str, body: object = None) -> object:
        """Blocking REST call on a pooled keep-alive connection (a new TLS handshake costs ~380 ms on the Zero,
        a reused connection ~70 ms). A pooled connection the hub has since closed is replaced once."""
        assert self._settings is not None
        for attempt in range(2):
            try:
                connection, reused = self._pool.get_nowait(), True
            except queue.Empty:
                connection, reused = self._connect(), False
            try:
                connection.request(
                    method,
                    f"/v1{path}",
                    body=json.dumps(body) if body is not None else None,
                    headers={"Authorization": f"Bearer {self._settings['token']}", "Content-Type": "application/json"},
                )
                response = connection.getresponse()
                data = response.read()
            except (http.client.HTTPException, OSError):
                connection.close()
                if reused and attempt == 0:
                    continue  # stale keep-alive connection: retry on a fresh one
                raise
            self._pool.put(connection)
            if response.status >= 400:
                raise ConnectionError(f"{method} {path}: HTTP {response.status}")
            return json.loads(data) if data else None
        raise ConnectionError(f"{method} {path}: no connection")

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._commands = asyncio.Queue()
        assert self._settings is not None
        url = f"wss://{self._settings['host']}:{PORT}/v1"
        delay = BACKOFF_START_S
        while True:
            try:
                async with websockets.connect(
                    url,
                    ssl=self._context(),
                    additional_headers={"Authorization": f"Bearer {self._settings['token']}"},
                    ping_interval=20,
                    open_timeout=10,
                ) as ws:
                    self._check_certificate(ws.transport.get_extra_info("ssl_object").getpeercert(binary_form=True))
                    devices = await asyncio.to_thread(self._request, "GET", "/devices")
                    self._lamps = {
                        d["id"]: parse_lamp(d)
                        for d in devices  # type: ignore[union-attr]
                        if d.get("deviceType") == "light" and d.get("room", {}).get("name", "").casefold() == self._room
                    }
                    log.info("lights: %d lamps in room %r", len(self._lamps), self._room)
                    self._post_state()
                    delay = BACKOFF_START_S
                    sender = asyncio.create_task(self._send_loop())
                    try:
                        async for raw in ws:
                            self._handle_event(json.loads(raw))
                    finally:
                        sender.cancel()
            except Exception as error:  # network, TLS, HTTP: report and retry
                log.warning("lights: %s", error or type(error).__name__)
            self._inbox.put({"type": LIGHTS_UNAVAILABLE})
            self._wake()
            await asyncio.sleep(delay)
            delay = min(delay * 2, BACKOFF_MAX_S)

    def _handle_event(self, event: dict) -> None:
        if event.get("type") != "deviceStateChanged":
            return
        data = event.get("data", {})
        lamp = self._lamps.get(data.get("id", ""))
        if lamp is None:
            return
        changed = False
        for attribute, key in _ATTRIBUTES.items():
            if attribute in data.get("attributes", {}):
                lamp[key] = data["attributes"][attribute]
                changed = True
        if "isReachable" in data:
            lamp["reachable"] = bool(data["isReachable"])
            changed = True
        if changed:
            self._post_state()

    async def _send_loop(self) -> None:
        assert self._commands is not None
        while True:
            # Coalesce what's queued: the latest value per lamp and attribute wins (slider drags).
            pending: dict[str, dict] = {}
            lamp_id, attributes = await self._commands.get()
            pending.setdefault(lamp_id, {}).update(attributes)
            while not self._commands.empty():
                lamp_id, attributes = self._commands.get_nowait()
                pending.setdefault(lamp_id, {}).update(attributes)
            # One attribute per PATCH. All lamps get each attribute at the same time (parallel requests, each on
            # its own pooled connection) so they change together; isOn goes first so a level/temperature shows.
            names = sorted({name for attributes in pending.values() for name in attributes}, key=lambda n: n != "isOn")
            for name in names:
                targets = [(lamp_id, attributes[name]) for lamp_id, attributes in pending.items() if name in attributes]
                results = await asyncio.gather(
                    *(asyncio.to_thread(self._request, "PATCH", f"/devices/{lamp_id}", [{"attributes": {name: value}}])
                      for lamp_id, value in targets),
                    return_exceptions=True,
                )
                for (lamp_id, _), result in zip(targets, results):
                    if isinstance(result, BaseException):
                        log.warning("lights: PATCH %s %s failed: %s", lamp_id, name, result)

    def _post_state(self) -> None:
        lamps = sorted((dict(lamp) for lamp in self._lamps.values()), key=lambda lamp: lamp["name"])
        self._inbox.put({"type": LIGHTS_STATE, "lamps": lamps})
        self._wake()
