"""Tests for endpoint resolution: the "usb" endpoint and reachability probing.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import asyncio
import ipaddress
import unittest
from unittest import mock

from deck import net


class ResolveTests(unittest.TestCase):
    def test_normal_endpoints_are_unchanged(self) -> None:
        self.assertEqual(asyncio.run(net.resolve("ws://192.168.1.20:8765")), "ws://192.168.1.20:8765")

    def test_usb_endpoint_is_skipped_while_the_link_is_down(self) -> None:
        with mock.patch.object(net, "_iface_network", return_value=None):
            self.assertIsNone(asyncio.run(net.resolve("ws://usb:8765")))

    def test_usb_endpoint_finds_the_host_that_answers(self) -> None:
        link = ipaddress.IPv4Interface("10.12.194.1/28")
        probed: list[str] = []

        async def fake_reachable(url: str) -> bool:
            probed.append(url)
            return url == "ws://10.12.194.7:8765"

        with mock.patch.object(net, "_iface_network", return_value=link), \
                mock.patch.object(net, "reachable", fake_reachable):
            self.assertEqual(asyncio.run(net.resolve("ws://usb:8765")), "ws://10.12.194.7:8765")
        self.assertNotIn("ws://10.12.194.1:8765", probed, "the Pi's own address isn't probed")
        self.assertEqual(len(probed), 13)

    def test_usb_endpoint_without_server_is_none(self) -> None:
        async def nobody(url: str) -> bool:
            return False

        with mock.patch.object(net, "_iface_network", return_value=ipaddress.IPv4Interface("10.12.194.1/28")), \
                mock.patch.object(net, "reachable", nobody):
            self.assertIsNone(asyncio.run(net.resolve("ws://usb:8765")))

    def test_unreachable_port_is_false(self) -> None:
        self.assertFalse(asyncio.run(net.reachable("ws://127.0.0.1:9")))


if __name__ == "__main__":
    unittest.main()
