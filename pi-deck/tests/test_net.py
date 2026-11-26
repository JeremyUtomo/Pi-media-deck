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


class LanTests(unittest.TestCase):
    """ "ws://lan:8765": the PC wherever it is on the Pi's own network (its DHCP address can change)."""

    def setUp(self) -> None:
        net._last_found.clear()
        self.patches = [
            mock.patch.object(net, "_default_iface", return_value="wlan0"),
            mock.patch.object(net, "_iface_network", return_value=ipaddress.IPv4Interface("192.168.1.64/24")),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self.patches:
            patch.stop()

    def answering(self, *hosts: str):
        probed = []

        async def reachable(url: str) -> bool:
            probed.append(url)
            return any(f"//{host}:" in url for host in hosts)

        return mock.patch.object(net, "reachable", reachable), probed

    def test_finds_the_pc_anywhere_on_the_network(self) -> None:
        patch, probed = self.answering("192.168.1.17")
        with patch:
            self.assertEqual(asyncio.run(net.resolve("ws://lan:8765")), "ws://192.168.1.17:8765")
        self.assertEqual(len(probed), 253, "every other host of the /24")
        self.assertNotIn("ws://192.168.1.64:8765", probed, "not the Pi itself")

    def test_tries_the_last_address_first(self) -> None:
        patch, probed = self.answering("192.168.1.17")
        with patch:
            asyncio.run(net.resolve("ws://lan:8765"))
            probed.clear()
            self.assertEqual(asyncio.run(net.resolve("ws://lan:8765")), "ws://192.168.1.17:8765")
        self.assertEqual(probed, ["ws://192.168.1.17:8765"], "no scan while it still answers")

    def test_follows_a_new_address(self) -> None:
        patch, _ = self.answering("192.168.1.98")
        with patch:
            asyncio.run(net.resolve("ws://lan:8765"))
        patch, _ = self.answering("192.168.1.17")  # the router gave the PC a new address
        with patch:
            self.assertEqual(asyncio.run(net.resolve("ws://lan:8765")), "ws://192.168.1.17:8765")

    def test_no_network_or_no_pc(self) -> None:
        patch, _ = self.answering()
        with patch:
            self.assertIsNone(asyncio.run(net.resolve("ws://lan:8765")))
        with mock.patch.object(net, "_default_iface", return_value=None):
            self.assertIsNone(asyncio.run(net.resolve("ws://lan:8765")))


if __name__ == "__main__":
    unittest.main()
