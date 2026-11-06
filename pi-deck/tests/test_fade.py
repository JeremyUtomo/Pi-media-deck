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


class PlayingRedrawTests(unittest.TestCase):
    """A playing track redraws the Spotify tab once a second (when m:ss changes), not at the frame cap."""

    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        store = Store()
        store.apply({"type": "net.connected", "endpoint": "test"})
        store.apply({"type": "media.state", "available": True, "title": "Song", "artist": "Artist", "album": "Album",
                     "durationMs": 200000, "positionMs": 1000, "positionUpdatedAt": 0, "isPlaying": True,
                     "canPrevious": True, "canNext": True, "artHash": None})
        self.clock = FakeClock()
        self.patch = mock.patch.object(app_module.time, "monotonic", self.clock)
        self.patch.start()
        self.app = App(self.screen, store, mock.Mock(), Fonts())  # starts on Spotify

    def tearDown(self) -> None:
        self.patch.stop()
        pygame.quit()

    def test_waits_for_the_next_second(self) -> None:
        self.app.draw()
        self.assertFalse(self.app.needs_draw(), "not again right away (that kept the Pi at 35 % + 83 % CPU)")
        self.assertGreater(self.app.wait_ms(), 0)
        self.assertLessEqual(self.app.wait_ms(), 1010)
        self.clock.now += 1.01
        self.assertTrue(self.app.needs_draw(), "the elapsed time has ticked over")


class LensSlideTests(unittest.TestCase):
    """Picking a tab: the page switches at once, the selection lens glides over."""

    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        store = Store()
        store.apply({"type": "net.connected", "endpoint": "test"})
        self.clock = FakeClock()
        self.patch = mock.patch.object(app_module.time, "monotonic", self.clock)
        self.patch.start()
        self.app = App(self.screen, store, mock.Mock(), Fonts())  # starts on Spotify (tab 1)
        self.app.draw()

    def tearDown(self) -> None:
        self.patch.stop()
        pygame.quit()

    def tap_tab(self, index: int) -> None:
        hit = next(h for h in self.app._hits if h.key == f"tab:{index}")
        self.app.pointer_down(hit.rect.center)
        self.app.pointer_up(hit.rect.center)

    def test_page_switches_at_once_and_the_lens_glides(self) -> None:
        start = self.app._lens_x
        self.tap_tab(0)
        self.assertEqual(self.app._active, 0, "the page itself doesn't animate")
        self.assertTrue(self.app.needs_draw())
        self.assertLessEqual(self.app.wait_ms(), 1, "no idle wait between slide frames")

        self.clock.now += app_module.LENS_SLIDE_S / 2
        self.app.draw()
        middle = self.app._lens_x
        self.app.draw()  # same moment: same place
        self.assertEqual(self.app._lens_x, middle)

        self.clock.now += app_module.LENS_SLIDE_S
        self.app.draw()
        end = self.app._lens_x
        self.assertLess(end, middle)
        self.assertLess(middle, start)
        self.assertGreater(start - middle, middle - end, "ease-out: most of the way in the first half")
        self.assertFalse(self.app.needs_draw(), "at rest: back to redraw-on-change")

    def test_the_slide_redraws_only_the_bar(self) -> None:
        self.tap_tab(0)
        self.app.draw()  # the tap: one full redraw, the new page
        self.clock.now += app_module.LENS_SLIDE_S / 3
        self.assertTrue(self.app.lens_moving)
        self.assertFalse(self.app.needs_draw(), "no full frames while only the lens moves")
        before = self.app._lens_x
        rects = self.app.draw_frame()
        self.assertEqual(rects, [pygame.Rect(0, 0, theme.WIDTH, theme.TAB_BAR_HEIGHT)])
        self.assertNotEqual(self.app._lens_x, before)
        self.clock.now += app_module.LENS_SLIDE_S
        self.app.draw_frame()
        self.assertFalse(self.app.lens_moving)

    def test_tapping_the_current_tab_doesnt_animate(self) -> None:
        self.tap_tab(1)
        self.app.draw()
        self.assertFalse(self.app.needs_draw())


if __name__ == "__main__":
    unittest.main()
