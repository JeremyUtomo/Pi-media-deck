"""WebSocket client on its own thread. Talks to the pygame loop only through queues."""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import queue
import socket
import struct
import sys
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path

import websockets

from deck import CLIENT_NAME, PROTOCOL_VERSION

log = logging.getLogger(__name__)

# Synthetic messages the net thread posts to the inbox alongside the server's own.
NET_CONNECTED = "net.connected"
NET_DISCONNECTED = "net.disconnected"

OPEN_TIMEOUT_S = 3.0
USB_HOST = "usb"  # endpoint host meaning "the PC on the USB link" (see resolve())
USB_IFACE = "usb0"
MAX_USB_PROBES = 30
PROBE_TIMEOUT_S = 0.6
PREFERRED_CHECK_S = 15.0  # while on a fallback endpoint, how often to look for a preferred one
SIOCGIFADDR = 0x8915
SIOCGIFNETMASK = 0x891B
BACKOFF_START_S = 1.0
BACKOFF_MAX_S = 10.0
MAX_MESSAGE_BYTES = 8 * 1024 * 1024
BINARY_MESSAGES = ("media.art", "audio.icon")


class NetClient:
    """Connects to the first reachable endpoint, reconnects with backoff, and forwards every message to `inbox`.

    `wake` is called after each message so the UI loop can stop waiting for events.
    """

    def __init__(self, endpoints: list[str], inbox: queue.Queue[dict], wake: Callable[[], None]) -> None:
        self._endpoints = endpoints
        self._inbox = inbox
        self._wake = wake
        self._loop: asyncio.AbstractEventLoop | None = None
        self._outbox: asyncio.Queue[dict] | None = None
        self._main_task: asyncio.Task | None = None
        self._thread = threading.Thread(target=self._thread_main, name="net", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def send(self, message: dict) -> None:
        """Queues a message for the server. Dropped if not connected (commands are never replayed later)."""
        loop, outbox = self._loop, self._outbox
        if loop is not None and outbox is not None:
            loop.call_soon_threadsafe(outbox.put_nowait, message)

    def stop(self) -> None:
        if self._loop is not None and self._main_task is not None:
            self._loop.call_soon_threadsafe(self._main_task.cancel)
        self._thread.join(timeout=2)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._main())
        except asyncio.CancelledError:
            pass

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._outbox = asyncio.Queue()
        self._main_task = asyncio.current_task()

        delay = BACKOFF_START_S
        while True:
            for index, url in enumerate(self._endpoints):
                resolved = await resolve(url)
                if resolved is None:
                    continue  # e.g. the USB link isn't up: skip without waiting for a timeout
                if await self._try_endpoint(resolved, preferred=self._endpoints[:index]):
                    # Was connected and dropped: start over from the preferred endpoint right away.
                    delay = BACKOFF_START_S
                    break
            else:
                await asyncio.sleep(delay)
                delay = min(delay * 2, BACKOFF_MAX_S)

    async def _try_endpoint(self, url: str, preferred: list[str]) -> bool:
        """Returns True if a session was established (and has now ended).

        While connected through a fallback, checks the `preferred` endpoints now and then and drops this
        connection once one is reachable, so the deck moves back to USB when the cable comes up.
        """
        connected = False
        try:
            async with websockets.connect(
                url,
                open_timeout=OPEN_TIMEOUT_S,
                max_size=MAX_MESSAGE_BYTES,
                ping_interval=5,
                ping_timeout=5,
                compression=None,
            ) as ws:
                connected = True
                log.info("connected to %s", url)
                self._clear_outbox()
                self._post({"type": NET_CONNECTED, "endpoint": url})
                await ws.send(json.dumps({"type": "hello", "protocol": PROTOCOL_VERSION, "client": CLIENT_NAME}))
                watcher = asyncio.create_task(self._watch_preferred(ws, preferred)) if preferred else None
                try:
                    await self._session(ws)
                finally:
                    if watcher is not None:
                        watcher.cancel()
        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException) as error:
            log.info("%s: %s", url, error or type(error).__name__)
        finally:
            if connected:
                log.info("disconnected from %s", url)
                self._post({"type": NET_DISCONNECTED})
        return connected

    async def _watch_preferred(self, ws: websockets.ClientConnection, preferred: list[str]) -> None:
        while True:
            await asyncio.sleep(PREFERRED_CHECK_S)
            for url in preferred:
                resolved = await resolve(url)
                if resolved is not None and await reachable(resolved):
                    log.info("%s is reachable again; switching to it", resolved)
                    await ws.close()
                    return

    async def _session(self, ws: websockets.ClientConnection) -> None:
        sender = asyncio.create_task(self._send_loop(ws))
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("ignoring non-JSON message")
                    continue
                if message.get("type") in BINARY_MESSAGES:
                    message["bytes"] = base64.b64decode(message.pop("data", ""))
                self._post(message)
        finally:
            sender.cancel()

    async def _send_loop(self, ws: websockets.ClientConnection) -> None:
        assert self._outbox is not None
        while True:
            message = await self._outbox.get()
            await ws.send(json.dumps(message))

    def _clear_outbox(self) -> None:
        assert self._outbox is not None
        while not self._outbox.empty():
            self._outbox.get_nowait()

    def _post(self, message: dict) -> None:
        self._inbox.put(message)
        self._wake()


# --- USB link ------------------------------------------------------------------------------------------------
#
# With `rpi-usb-gadget on` the Pi is a USB network device: usb0 gets 10.12.194.1/28 and NetworkManager's
# dnsmasq hands the PC an address in that /28 by DHCP. The PC's address can change, so the endpoint
# "ws://usb:<port>" means "whichever host on usb0's subnet answers on <port>", found by probing them all at once.


def _iface_network(name: str) -> ipaddress.IPv4Interface | None:
    """usb0's address and subnet if the link is up (Linux only; None elsewhere or while unplugged)."""
    if sys.platform != "linux":
        return None
    try:
        if Path(f"/sys/class/net/{name}/operstate").read_text().strip() != "up":
            return None
        import fcntl

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            request = struct.pack("256s", name.encode()[:15])
            address = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), SIOCGIFADDR, request)[20:24])
            netmask = socket.inet_ntoa(fcntl.ioctl(sock.fileno(), SIOCGIFNETMASK, request)[20:24])
        return ipaddress.IPv4Interface(f"{address}/{netmask}")
    except OSError:
        return None  # no such interface, or no address yet


async def reachable(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(parts.hostname, parts.port or 80), PROBE_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


async def resolve(url: str) -> str | None:
    """The URL to connect to: unchanged, or for host "usb" the PC found on the USB link (None if not found)."""
    parts = urllib.parse.urlsplit(url)
    if parts.hostname != USB_HOST:
        return url
    network = _iface_network(USB_IFACE)
    if network is None:
        return None
    candidates = [str(host) for host in network.network.hosts() if host != network.ip][:MAX_USB_PROBES]
    port = parts.port or 80
    results = await asyncio.gather(*(reachable(f"ws://{host}:{port}") for host in candidates))
    found = next((host for host, ok in zip(candidates, results) if ok), None)
    return parts._replace(netloc=f"{found}:{port}").geturl() if found else None
