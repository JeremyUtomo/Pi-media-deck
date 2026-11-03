"""Tests for the PC power button and its confirmation dialog. Nothing here ever reaches a real PC.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import os
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck.app import App  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import power, theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402

SHUTDOWN = {"type": "pc.power", "action": "shutdown"}


class FakeNet:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, message: dict) -> None:
        self.sent.append(message)


class PowerDialogTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.net = FakeNet()
        self.app = App(self.screen, self.store, self.net, Fonts())  # type: ignore[arg-type]
        self.app.draw()

    def tearDown(self) -> None:
        pygame.quit()

    def tap(self, pos: tuple[float, float]) -> None:
        self.app.pointer_down(pos)
        self.app.pointer_up(pos)
        self.app.draw()

    def tap_key(self, key: str) -> None:
        self.tap(next(h.rect for h in reversed(self.app._hits) if h.key == key).center)

    def open_dialog(self) -> None:
        self.tap_key(power.BUTTON_KEY)
        self.assertTrue(self.app._power.open)
        self.assertEqual(self.net.sent, [], "opening the dialog sends nothing")

    def test_confirm_sends_shutdown(self) -> None:
        self.open_dialog()
        self.tap_key(power.CONFIRM_KEY)
        self.assertEqual(self.net.sent, [SHUTDOWN])
        self.assertFalse(self.app._power.open)

    def test_cancel_outside_and_card_taps_never_shut_down(self) -> None:
        self.open_dialog()
        card = next(h.rect for h in self.app._hits if h.key == power.CARD_KEY)
        self.tap((card.centerx, card.top + 40))  # on the dialog text: stays open
        self.assertTrue(self.app._power.open)
        self.tap((20, 460))  # outside: cancels
        self.assertFalse(self.app._power.open)
        self.open_dialog()
        self.tap_key(power.CANCEL_KEY)
        self.assertFalse(self.app._power.open)
        self.assertEqual(self.net.sent, [])

    def test_dialog_blocks_the_tabs_underneath(self) -> None:
        self.open_dialog()
        keys = {h.key for h in self.app._hits}
        self.assertTrue(all(key.startswith("power:") for key in keys))

    def test_no_button_while_disconnected(self) -> None:
        self.store.apply({"type": "net.disconnected"})
        self.app.draw()
        self.assertNotIn(power.BUTTON_KEY, [h.key for h in self.app._hits])


if __name__ == "__main__":
    unittest.main()
