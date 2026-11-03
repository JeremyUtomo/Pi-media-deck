"""Headless tests for tab gestures: taps, throttled slider drags, mute, mixer scrolling.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import os
import time
import unittest

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck.app import App  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import theme, volume  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402


class FakeNet:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, message: dict) -> None:
        self.sent.append(message)


def audio_state(session_count: int = 2) -> dict:
    return {
        "type": "audio.state",
        "master": {"volume": 0.5, "muted": False},
        "sessions": [
            {"id": f"app{i}.exe", "name": f"App {i}", "volume": 1.0, "muted": False, "iconHash": None}
            for i in range(session_count)
        ],
    }


TRACK_BOTTOM = theme.TAB_BAR_HEIGHT + volume.TRACK_TOP + volume.TRACK_HEIGHT


def track_y(value: float) -> float:
    return TRACK_BOTTOM - value * volume.TRACK_HEIGHT


class GestureTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.net = FakeNet()
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.app = App(self.screen, self.store, self.net, Fonts())  # type: ignore[arg-type]
        self.app.select_tab("volume")

    def tearDown(self) -> None:
        pygame.quit()

    def load(self, sessions: int = 2) -> None:
        self.store.apply(audio_state(sessions))
        self.app.draw()

    def strip_x(self, index: int, count: int) -> float:
        total = count * volume.STRIP_WIDTH
        left = (theme.WIDTH - total) // 2 if total <= theme.WIDTH else 0
        return left + index * volume.STRIP_WIDTH + volume.STRIP_WIDTH / 2

    def test_tap_on_slider_sets_volume(self) -> None:
        self.load()
        x = self.strip_x(0, 3)
        self.app.pointer_down((x, track_y(0.25)))
        self.app.pointer_up((x, track_y(0.25)))
        self.assertEqual(self.net.sent, [{"type": "audio.set_volume", "target": "master", "volume": 0.25}])

    def test_drag_is_throttled_and_final_value_sent(self) -> None:
        self.load()
        x = self.strip_x(1, 3)
        self.app.pointer_down((x, track_y(0.1)))
        for step in range(1, 21):  # 20 moves within a few ms
            self.app.pointer_move((x, track_y(0.1 + step * 0.04)))
        self.app.pointer_up((x, track_y(0.9)))

        volumes = [m["volume"] for m in self.net.sent]
        self.assertTrue(all(m["target"] == "app0.exe" for m in self.net.sent))
        self.assertLess(len(volumes), 5, "moves inside 50 ms must be coalesced")
        self.assertEqual(volumes[-1], 0.9)

    def test_pending_value_flushed_by_tick(self) -> None:
        self.load()
        x = self.strip_x(1, 3)
        self.app.pointer_down((x, track_y(0.2)))
        self.app.pointer_move((x, track_y(0.5)))  # first move: sent immediately
        self.app.pointer_move((x, track_y(0.6)))  # throttled: pending
        sent_before = len(self.net.sent)
        time.sleep(volume.SEND_INTERVAL_S + 0.04)  # margin for coarse timers when the whole suite runs
        self.app.tick()
        self.assertEqual(len(self.net.sent), sent_before + 1)
        self.assertEqual(self.net.sent[-1]["volume"], 0.6)

    def test_dragged_value_shown_until_echo(self) -> None:
        self.load()
        x = self.strip_x(0, 3)
        self.app.pointer_down((x, track_y(0.5)))
        self.app.pointer_move((x, track_y(0.8)))
        self.store.apply(audio_state())  # stale echo still says 0.5
        tab = self.app._tab
        assert isinstance(tab, volume.VolumeTab)
        self.assertAlmostEqual(tab._overrides["master"][0], 0.8, places=2)

    def test_mute_toggles(self) -> None:
        self.load()
        x = self.strip_x(2, 3)
        y = theme.HEIGHT - 30
        self.app.pointer_down((x, y))
        self.app.pointer_up((x, y))
        self.assertEqual(self.net.sent, [{"type": "audio.set_mute", "target": "app1.exe", "muted": True}])

    def test_horizontal_swipe_scrolls_without_changing_volume(self) -> None:
        self.load(sessions=9)  # 10 strips, more than fit
        tab = self.app._tab
        assert isinstance(tab, volume.VolumeTab)
        y = track_y(0.5)
        self.app.pointer_down((600, y))
        self.app.pointer_move((560, y + 2))
        self.app.pointer_move((300, y + 4))
        self.app.pointer_up((300, y + 4))
        self.app.draw()
        self.assertEqual(self.net.sent, [])
        self.assertEqual(tab._scroll % volume.STRIP_WIDTH, 0, "scroll snaps to a strip")
        self.assertGreater(tab._scroll, 0)

    def test_tab_bar_switches_tabs(self) -> None:
        self.load()
        self.app.pointer_down((300, 30))
        self.app.pointer_up((300, 30))
        self.assertEqual(self.app._tab.title, "Spotify")


if __name__ == "__main__":
    unittest.main()
