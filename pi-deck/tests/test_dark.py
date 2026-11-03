"""Tests for going fully dark (black screen, backlight off) when idle and the PC is off.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck import app as app_module  # noqa: E402
from deck.app import App  # noqa: E402
from deck.backlight import Backlight  # noqa: E402
from deck.idle import IdleTimer  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402

BLACK = (0, 0, 0, 255)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeBacklight:
    def __init__(self) -> None:
        self.on = True

    def set(self, on: bool) -> None:
        self.on = on


class DarkTests(unittest.TestCase):
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
        self.backlight = FakeBacklight()
        self.app = App(self.screen, self.store, mock.Mock(), Fonts(), self.idle,
                       backlight=self.backlight, dark_after_s=30)
        self.app.draw()

    def tearDown(self) -> None:
        self.patch.stop()
        pygame.quit()

    def step(self, seconds: float) -> None:
        """Advances time, then runs the loop's tick + draw (twice, so a fade that just ended is drawn)."""
        self.clock.now += seconds
        for _ in range(2):
            self.app.tick()
            if self.app.needs_draw():
                self.app.draw()

    def pc_off(self) -> None:
        self.store.apply({"type": "net.disconnected"})

    def test_pc_off_while_idle_goes_dark_after_the_grace_period(self) -> None:
        self.step(11)
        self.assertTrue(self.app.ambient)
        self.pc_off()
        self.step(5)
        self.assertFalse(self.app.dark, "a short Wi-Fi blip doesn't blank the screen")
        self.assertTrue(self.backlight.on)

        self.step(30)
        self.assertTrue(self.app.dark)
        self.step(app_module.FADE_TO_AMBIENT_S)
        self.assertEqual(self.screen.get_at((400, 240)), BLACK)
        self.assertFalse(self.backlight.on, "backlight off once faded to black")
        self.assertFalse(self.app.needs_draw())

    def test_pc_off_while_in_use_waits_for_idle(self) -> None:
        self.pc_off()
        self.step(5)
        self.step(5)  # 10 s without touch: idle, and the PC has been gone only 10 s
        self.assertTrue(self.app.ambient)
        self.assertTrue(self.backlight.on)
        self.step(25)
        self.step(app_module.FADE_TO_AMBIENT_S)
        self.assertFalse(self.backlight.on)

    def test_touch_wakes_it_with_backlight_on_right_away(self) -> None:
        self.pc_off()
        self.step(11)
        self.step(30)
        self.step(app_module.FADE_TO_AMBIENT_S)
        self.assertFalse(self.backlight.on)

        self.app.pointer_down((400, 300))
        self.assertTrue(self.backlight.on, "lit before the fade in starts")
        self.app.pointer_up((400, 300))
        self.step(app_module.FADE_FROM_AMBIENT_S + 0.01)
        self.assertFalse(self.app.dark)
        self.assertNotEqual(self.screen.get_at((400, 20)), BLACK, "the deck UI ('Connecting to PC…')")

        self.step(11)  # idle again, PC still off: dark again
        self.step(app_module.FADE_TO_AMBIENT_S)
        self.assertFalse(self.backlight.on)

    def test_pc_coming_back_lights_the_album_cover_again(self) -> None:
        self.pc_off()
        self.step(11)
        self.step(30)
        self.step(app_module.FADE_TO_AMBIENT_S)
        self.assertFalse(self.backlight.on)

        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.step(0)
        self.assertTrue(self.backlight.on)
        self.assertTrue(self.app.ambient, "back to the ambient view, not the tabs")
        self.assertFalse(self.app.dark)

    def test_disabled_with_zero(self) -> None:
        app = App(self.screen, self.store, mock.Mock(), Fonts(), self.idle, backlight=self.backlight, dark_after_s=0)
        self.pc_off()
        self.clock.now += 100
        app.tick()
        self.assertTrue(app.ambient)
        self.assertFalse(app.dark)


class BacklightTests(unittest.TestCase):
    def test_writes_max_and_zero_once_per_change(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            device = Path(root) / "backlight"
            device.mkdir()
            (device / "max_brightness").write_text("1\n")
            (device / "brightness").write_text("1\n")
            backlight = Backlight(Path(root))
            backlight.set(False)
            self.assertEqual((device / "brightness").read_text(), "0")
            backlight.set(True)
            self.assertEqual((device / "brightness").read_text(), "1")

    def test_no_backlight_is_a_no_op(self) -> None:
        backlight = Backlight(Path(tempfile.gettempdir()) / "no-such-backlight-dir")
        backlight.set(False)
        self.assertFalse(backlight.on)


if __name__ == "__main__":
    unittest.main()
