"""Tests for the Polarity tab: shown only while Polarity runs, playing controls, browsing rows.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import os
import time
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck.app import App  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402


def card(i: int, progress: float | None = None) -> dict:
    return {"id": f"tt000000{i}", "title": f"Film {i}", "subtitle": None, "posterHash": None, "progress": progress}


def state(running: bool = True, playing: dict | None = None, cont: list | None = None, library: list | None = None) -> dict:
    return {"type": "polarity.state", "running": running, "playing": playing,
            "continue": cont if cont is not None else [], "library": library if library is not None else []}


PLAYING = {"title": "Sintel", "subtitle": "S1 E2", "posterHash": None, "durationMs": 888000, "positionMs": 312000,
           "positionUpdatedAt": 0, "isPlaying": True}


class PolarityTabTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.net = mock.Mock()
        self.app = App(self.screen, self.store, self.net, Fonts())
        self.app.draw()

    def tearDown(self) -> None:
        pygame.quit()

    def tab_titles(self) -> list[str]:
        return [self.app._tabs[int(h.key.removeprefix("tab:"))].title for h in self.app._hits if h.key.startswith("tab:")]

    def hit(self, key: str):
        return next(h for h in self.app._hits if h.key == key)

    def tap(self, key: str) -> None:
        center = self.hit(key).rect.center
        self.app.pointer_down(center)
        self.app.pointer_up(center)
        self.app.draw()

    def test_tab_only_while_polarity_runs(self) -> None:
        self.assertNotIn("Polarity", self.tab_titles())
        self.store.apply(state(running=True))
        self.app.dirty = True
        self.app.draw()
        self.assertEqual(self.tab_titles(), ["Volume", "Spotify", "Polarity"])

    def test_closing_polarity_falls_back_to_spotify(self) -> None:
        self.store.apply(state(running=True))
        self.app.select_tab("polarity")
        self.app.draw()
        self.store.apply(state(running=False))
        self.app.tick()
        self.app.draw()
        self.assertEqual(self.app._tab.title, "Spotify")
        self.assertNotIn("Polarity", self.tab_titles())

    def test_pc_gone_hides_it(self) -> None:
        self.store.apply(state(running=True))
        self.store.apply({"type": "net.disconnected"})
        self.assertIsNone(self.store.polarity)

    def test_playing_controls_send_commands(self) -> None:
        self.store.apply(state(playing={**PLAYING, "positionUpdatedAt": int(time.time() * 1000)}))
        self.app.select_tab("polarity")
        self.app.draw()
        self.tap("play_pause")
        sent = [c.args[0] for c in self.net.send.call_args_list]
        self.assertEqual([m["action"] for m in sent if m["type"] == "polarity.command"], ["play_pause"])
        self.assertFalse(any(h.key.startswith("seek") for h in self.app._hits), "no skip buttons: Polarity undoes them")
        self.assertTrue(self.app._tab.animating())

    def test_browse_rows_scroll_separately(self) -> None:
        self.store.apply(state(cont=[card(i, 0.4) for i in range(10)], library=[card(i) for i in range(12)]))
        self.app.select_tab("polarity")
        self.app.draw()
        tab = self.app._tab
        row = self.hit("scroll:continue").rect
        start = (row.right - 40, row.centery)
        self.app.pointer_down(start)
        for dx in range(0, 200, 20):
            self.app.pointer_move((start[0] - dx, start[1]))
        self.app.pointer_up((start[0] - 200, start[1]))
        self.assertGreater(tab._scroll["continue"], 100)
        self.assertEqual(tab._scroll["library"], 0, "only the row that was swiped")

    def test_nothing_at_all(self) -> None:
        self.store.apply(state())
        self.app.select_tab("polarity")
        self.app.draw()
        self.assertFalse(any(h.key.startswith("scroll:") for h in self.app._hits))

    def test_posters_are_cached_by_hash(self) -> None:
        self.store.apply({"type": "polarity.art", "hash": "abc", "bytes": b"jpeg"})
        self.assertEqual(self.store.posters.get("abc"), b"jpeg")


if __name__ == "__main__":
    unittest.main()
