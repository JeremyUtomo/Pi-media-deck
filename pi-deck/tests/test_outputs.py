"""Tests for the audio output pill and selector popup.

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
from deck.ui import outputs, theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402

SPEAKERS = {"id": "{0.0.0.00000000}.{speakers}", "name": "Speakers (Realtek(R) Audio)"}
HEADSET = {"id": "{0.0.0.00000000}.{headset}", "name": "Headphones (Arctis 7)"}


class FakeNet:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, message: dict) -> None:
        self.sent.append(message)


class OutputSelectorTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.store.apply({"type": "audio.state", "master": {"volume": 0.5, "muted": False}, "sessions": [],
                          "output": SPEAKERS["id"], "outputs": [HEADSET, SPEAKERS]})
        self.net = FakeNet()
        self.app = App(self.screen, self.store, self.net, Fonts())  # type: ignore[arg-type]
        self.app.draw()

    def tearDown(self) -> None:
        pygame.quit()

    def tap(self, pos: tuple[float, float]) -> None:
        self.app.pointer_down(pos)
        self.app.pointer_up(pos)
        self.app.draw()

    def hit(self, key: str) -> pygame.Rect:
        return next(h.rect for h in self.app._hits if h.key == key)

    def open_menu(self) -> None:
        self.tap(self.hit(outputs.MENU_KEY).center)
        self.assertTrue(self.app._outputs.open)

    def test_pill_opens_and_selecting_switches_output(self) -> None:
        self.open_menu()
        keys = [h.key for h in self.app._hits]
        self.assertEqual(keys, [outputs.CLOSE_KEY, "output:0", "output:1"], "the popup takes all input")
        self.tap(self.hit("output:0").center)  # rows are in the PC's order: headset first
        self.assertEqual(self.net.sent, [{"type": "audio.set_output", "id": HEADSET["id"]}])
        self.assertFalse(self.app._outputs.open)

    def test_choosing_the_current_output_sends_nothing(self) -> None:
        self.open_menu()
        self.tap(self.hit("output:1").center)
        self.assertEqual(self.net.sent, [])
        self.assertFalse(self.app._outputs.open)

    def test_tapping_outside_closes_without_touching_the_tab(self) -> None:
        self.open_menu()
        self.tap((60, 300))  # over the Spotify tab's content
        self.assertFalse(self.app._outputs.open)
        self.assertEqual(self.net.sent, [])

    def test_split_name(self) -> None:
        self.assertEqual(outputs.split_name("Speakers (Realtek(R) Audio)"), ("Speakers", "Realtek(R) Audio"))
        self.assertEqual(outputs.split_name("6.5mm output (Focusrite USB Audio)"), ("6.5mm output", "Focusrite USB Audio"))
        self.assertEqual(outputs.split_name("Plain name"), ("Plain name", None))

    def test_disconnected_shows_status_without_menu(self) -> None:
        self.store.apply({"type": "net.disconnected"})
        self.app.draw()
        self.assertNotIn(outputs.MENU_KEY, [h.key for h in self.app._hits])


if __name__ == "__main__":
    unittest.main()
