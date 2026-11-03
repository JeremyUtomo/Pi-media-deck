"""Tests for Wake-on-LAN and the "Turn on PC" button.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck import app as app_module  # noqa: E402
from deck.app import WAKE_KEY, App  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402
from deck.wol import WakeOnLan, magic_packet  # noqa: E402


class MagicPacketTests(unittest.TestCase):
    def test_layout(self) -> None:
        packet = magic_packet("02-1A-2B-3C-4D-5E")
        self.assertEqual(len(packet), 102)
        self.assertEqual(packet[:6], b"\xff" * 6)
        self.assertEqual(packet[6:12], bytes.fromhex("021a2b3c4d5e"))
        self.assertEqual(packet[6:], bytes.fromhex("021a2b3c4d5e") * 16)
        self.assertEqual(magic_packet("02:1a:2b:3c:4d:5e"), packet)

    def test_rejects_garbage(self) -> None:
        with self.assertRaises(ValueError):
            magic_packet("02-1A-2B")

    def test_send_broadcasts_three_times(self) -> None:
        with mock.patch("deck.wol.socket.socket") as socket_class:
            sock = socket_class.return_value.__enter__.return_value
            self.assertTrue(WakeOnLan("02-1A-2B-3C-4D-5E", "192.168.1.255").send())
        self.assertEqual(sock.sendto.call_count, 3)
        self.assertEqual(sock.sendto.call_args.args[1], ("192.168.1.255", 9))


class TurnOnButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()  # never connected: the PC is off
        self.wol = mock.Mock()
        self.wol.send.return_value = True
        self.app = App(self.screen, self.store, mock.Mock(), Fonts(), wol=self.wol)
        self.app.draw()

    def tearDown(self) -> None:
        pygame.quit()

    def button(self):
        return next(hit for hit in self.app._hits if hit.key == WAKE_KEY)

    def tap(self, pos) -> None:
        self.app.pointer_down(pos)
        self.app.pointer_up(pos)
        self.app.tick()
        self.app.draw()

    def test_tap_sends_and_shows_starting_until_connected(self) -> None:
        self.tap(self.button().rect.center)
        self.wol.send.assert_called_once()
        self.assertIsNotNone(self.app._waking_until)

        self.tap(self.button().rect.center)  # "Send again"
        self.assertEqual(self.wol.send.call_count, 2)

        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.app.tick()
        self.assertIsNone(self.app._waking_until)

    def test_starting_state_times_out(self) -> None:
        self.tap(self.button().rect.center)
        with mock.patch.object(app_module.time, "monotonic", lambda: self.app._waking_until + 1):
            self.app.tick()
        self.assertIsNone(self.app._waking_until)

    def test_no_button_without_a_mac(self) -> None:
        app = App(self.screen, self.store, mock.Mock(), Fonts())
        app.draw()
        self.assertFalse(any(hit.key == WAKE_KEY for hit in app._hits))


if __name__ == "__main__":
    unittest.main()
