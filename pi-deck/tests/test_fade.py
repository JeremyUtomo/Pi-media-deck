"""Tests for the crossfade into and out of the ambient view.

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
from deck.app import App  # noqa: E402
from deck.idle import IdleTimer  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FadeTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.clock = FakeClock()
        self.patch = mock.patch.object(app_module.time, "monotonic", self.clock)
        self.patch.start()
        self.idle = IdleTimer(10, self.clock)
        self.app = App(self.screen, self.store, mock.Mock(), Fonts(), self.idle)
        self.app.draw()
        self.ui_pixel = self.screen.get_at((400, 20))  # tab bar background

    def tearDown(self) -> None:
        self.patch.stop()
        pygame.quit()

    def go_idle(self) -> None:
        self.clock.now += 11
        self.app.tick()

    def test_fades_into_ambient_over_about_a_second(self) -> None:
        self.go_idle()
        self.assertTrue(self.app.needs_draw())
        self.assertEqual(self.app.wait_ms(), app_module.FADE_WAIT_MS)

        self.app.draw()
        self.assertEqual(self.screen.get_at((400, 20)), self.ui_pixel, "starts from the current screen")

        self.clock.now += app_module.FADE_TO_AMBIENT_S / 2
        self.app.draw()
        middle = self.screen.get_at((400, 20))
        self.assertNotEqual(middle, self.ui_pixel, "half way it's a blend")

        self.clock.now += app_module.FADE_TO_AMBIENT_S
        self.app.draw()
        self.assertFalse(self.app.needs_draw(), "done: back to redraw-on-change")
        self.assertLess(sum(self.screen.get_at((400, 20))[:3]), sum(middle[:3]), "ends on the dark ambient view")

    def test_touch_fades_back_quickly_and_is_swallowed(self) -> None:
        self.go_idle()
        self.clock.now += 2
        self.app.draw()
        self.app.pointer_down((400, 300))
        self.app.pointer_up((400, 300))
        self.assertFalse(self.app.ambient)
        self.clock.now += app_module.FADE_FROM_AMBIENT_S + 0.01
        self.app.draw()
        self.assertEqual(self.screen.get_at((400, 20)), self.ui_pixel)
        self.assertFalse(self.app.needs_draw())


if __name__ == "__main__":
    unittest.main()
