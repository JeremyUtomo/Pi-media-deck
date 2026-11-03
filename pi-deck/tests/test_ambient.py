"""Tests for the ambient view: idle timing, the touch that leaves it being ignored, and rendering.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import io
import os
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck.app import App  # noqa: E402
from deck.idle import IdleTimer  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import ambient, theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeNet:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, message: dict) -> None:
        self.sent.append(message)


def media_state(art_hash: str | None = None) -> dict:
    return {"type": "media.state", "available": True, "title": "Song", "artist": "Artist", "album": "Album",
            "durationMs": 1000, "positionMs": 0, "positionUpdatedAt": 0, "isPlaying": True,
            "canPrevious": True, "canNext": True, "artHash": art_hash}


def white_jpeg() -> bytes:
    image = pygame.Surface((300, 300))
    image.fill((255, 255, 255))
    out = io.BytesIO()
    pygame.image.save(image, out, "art.jpg")
    return out.getvalue()


class IdleTimerTests(unittest.TestCase):
    def test_idle_after_timeout_and_touch_leaves(self) -> None:
        clock = FakeClock()
        timer = IdleTimer(60, clock)
        clock.now += 59
        self.assertFalse(timer.update())
        clock.now += 1
        self.assertTrue(timer.update(), "reports the switch once so the app redraws")
        self.assertFalse(timer.update())
        self.assertTrue(timer.activity(), "the touch that leaves idle mode is reported so it can be ignored")
        self.assertFalse(timer.activity())

    def test_touch_restarts_the_timer(self) -> None:
        clock = FakeClock()
        timer = IdleTimer(60, clock)
        clock.now += 50
        timer.activity()
        clock.now += 50
        self.assertFalse(timer.update())

    def test_zero_disables(self) -> None:
        clock = FakeClock()
        timer = IdleTimer(0, clock)
        clock.now += 100_000
        self.assertFalse(timer.update())


class AmbientAppTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.net = FakeNet()
        self.clock = FakeClock()
        self.idle = IdleTimer(10, self.clock)
        self.app = App(self.screen, self.store, self.net, Fonts(), self.idle, ambient_brightness=0.5)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        pygame.quit()

    def go_idle(self) -> None:
        self.app.draw()
        self.clock.now += 11
        self.app.tick()
        self.assertTrue(self.app.ambient)
        if self.app._fade is not None:  # skip the crossfade (covered by test_fade)
            self.app._fade.started -= 10
        self.app.draw()

    def test_touch_that_leaves_ambient_does_not_press_a_button(self) -> None:
        self.store.apply(media_state())
        play = (566, theme.TAB_BAR_HEIGHT + (theme.HEIGHT - theme.TAB_BAR_HEIGHT - 300) // 2 + 260)
        self.go_idle()
        self.assertFalse(self.app.animating(), "the progress bar doesn't redraw behind the ambient view")

        self.app.pointer_down(play)
        self.app.pointer_up(play)
        self.assertFalse(self.app.ambient)
        self.assertEqual(self.net.sent, [], "the waking tap is swallowed")

        self.app.draw()
        self.app.pointer_down(play)
        self.app.pointer_up(play)
        self.assertEqual(self.net.sent, [{"type": "media.command", "action": "play_pause"}])

    def test_cover_is_dimmed_and_background_darker(self) -> None:
        self.store.apply({"type": "media.art", "hash": "h1", "mime": "image/jpeg", "bytes": white_jpeg()})
        self.store.apply(media_state("h1"))
        self.go_idle()
        cover = self.screen.get_at((theme.WIDTH // 2, ambient.COVER_TOP + ambient.COVER_SIZE // 2))
        background = self.screen.get_at((20, 20))
        self.assertAlmostEqual(cover.r, 128, delta=6, msg="white cover at brightness 0.5")
        self.assertLess(background.r, cover.r // 2)
        self.assertGreater(background.r, 0)

    def test_clock_redraws_when_the_minute_changes(self) -> None:
        from unittest import mock

        from deck.ui import ambient as ambient_module

        self.store.apply(media_state())
        self.go_idle()
        minute = ["21:14"]
        with mock.patch.object(ambient_module.time, "strftime", lambda fmt: minute[0]):
            self.app.draw()
            self.app.tick()
            self.assertFalse(self.app.dirty, "same minute: nothing to redraw")
            minute[0] = "21:15"
            self.app.tick()
            self.assertTrue(self.app.dirty, "next minute: redraw the clock")

    def test_without_art_or_media_it_still_draws(self) -> None:
        self.store.apply(media_state("missing"))
        self.go_idle()
        self.store.apply({"type": "net.disconnected"})
        self.app.draw()


if __name__ == "__main__":
    unittest.main()
