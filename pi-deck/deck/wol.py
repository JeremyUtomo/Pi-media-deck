"""Wake-on-LAN: turns the PC on with a magic packet to its (wired) Ethernet adapter."""

from __future__ import annotations

import logging
import socket

log = logging.getLogger(__name__)

WOL_PORT = 9


def magic_packet(mac: str) -> bytes:
    """6 x 0xFF, then the MAC 16 times. Accepts "AA-BB-CC-DD-EE-FF" or "aa:bb:cc:dd:ee:ff"."""
    digits = mac.replace("-", "").replace(":", "").replace(".", "")
    address = bytes.fromhex(digits)
    if len(address) != 6:
        raise ValueError(f"not a MAC address: {mac!r}")
    return b"\xff" * 6 + address * 16


class WakeOnLan:
    def __init__(self, mac: str, broadcast: str = "255.255.255.255") -> None:
        self._packet = magic_packet(mac)
        self._broadcast = broadcast

    def send(self) -> bool:
        """Broadcasts the magic packet a few times (it's UDP; one can get lost on Wi-Fi). Never blocks."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setblocking(False)
                for _ in range(3):
                    sock.sendto(self._packet, (self._broadcast, WOL_PORT))
            log.info("sent Wake-on-LAN to %s", self._broadcast)
            return True
        except OSError as error:
            log.warning("Wake-on-LAN failed: %s", error)
            return False
