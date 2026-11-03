"""Tests for font fallback and the scrolling (marquee) song title.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck.state import Store  # noqa: E402
from deck.ui import spotify, theme  # noqa: E402
from deck.ui.spotify import SpotifyTab  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402


def media(title: str) -> dict:
    return {"type": "media.state", "available": True, "title": title, "artist": "Artist", "album": "Album",
            "durationMs": 1000, "positionMs": 0, "positionUpdatedAt": 0, "isPlaying": True,
            "canPrevious": True, "canNext": True, "artHash": None}


class ThemeTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.fonts = Fonts()

    def tearDown(self) -> None:
        pygame.quit()

    def test_bundled_font_is_used_and_missing_glyphs_fall_back(self) -> None:
        fredoka = theme.FONT_DIR / "Fredoka-Medium.ttf"
        coverage = theme._codepoints(fredoka)
        self.assertIn(ord("A"), coverage)
        self.assertIn(ord("é"), coverage)
        self.assertNotIn(ord("♪"), coverage)
        self.assertNotIn(ord("한"), coverage)

        self.assertEqual(self.fonts.runs("Hello"), [(fredoka, "Hello")])
        self.assertNotEqual(self.fonts.runs("♪")[0][0], fredoka, "a glyph Fredoka lacks switches font")
        self.assertNotEqual(self.fonts.runs("한", bold=True)[0][0], theme.FONT_DIR / "Fredoka-SemiBold.ttf")

    def test_mixed_scripts_are_split_into_runs(self) -> None:
        # Like "抱かれに来た女 — Kingo Hamada": the CJK font has no Latin, Fredoka has no CJK.
        fredoka = theme.FONT_DIR / "Fredoka-Medium.ttf"
        runs = self.fonts.runs("한국 노래 — Kingo Hamada")
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(runs[0][0], fredoka)
        self.assertEqual(runs[0][1], "한국 노래 ", "spaces stay with the run before them")
        self.assertEqual(runs[1], (fredoka, "— Kingo Hamada"))

        image = self.fonts.render("한국 노래 — Kingo Hamada", 18, (255, 255, 255))
        latin = self.fonts.render("— Kingo Hamada", 18, (255, 255, 255))
        self.assertGreater(image.get_width(), latin.get_width())

    def test_mixed_text_ellipsizes_to_width(self) -> None:
        image = self.fonts.render("한국 노래 — Kingo Hamada and a very long featured artist list", 18, (0, 0, 0), max_width=200)
        self.assertLessEqual(image.get_width(), 200)

    def test_short_title_does_not_scroll(self) -> None:
        store = Store()
        store.apply(media("Short"))
        tab = SpotifyTab(store, mock.Mock(), self.fonts)
        tab.draw(self.screen, pygame.Rect(0, 64, 800, 416), None)
        self.assertIsNone(tab.frame_interval_ms())
        self.assertEqual(tab.draw_frame(self.screen), [])

    def test_long_title_scrolls_after_pause(self) -> None:
        store = Store()
        store.apply(media("A really quite extraordinarily long song title (feat. Somebody) [Remastered 2024]"))
        tab = SpotifyTab(store, mock.Mock(), self.fonts)
        clock = [100.0]
        with mock.patch.object(spotify.time, "monotonic", lambda: clock[0]):
            tab.draw(self.screen, pygame.Rect(0, 64, 800, 416), None)
            self.assertEqual(tab.frame_interval_ms(), spotify.MARQUEE_FRAME_MS)
            rects = tab.draw_frame(self.screen)
            self.assertEqual(len(rects), 1)
            strip = rects[0]
            before = pygame.image.tobytes(self.screen.subsurface(strip), "RGB")

            clock[0] += spotify.MARQUEE_PAUSE_S - 0.5
            tab.draw_frame(self.screen)
            self.assertEqual(pygame.image.tobytes(self.screen.subsurface(strip), "RGB"), before, "still during the pause")

            clock[0] += 1.5
            tab.draw_frame(self.screen)
            self.assertNotEqual(pygame.image.tobytes(self.screen.subsurface(strip), "RGB"), before, "scrolling")

    def test_new_title_restarts_the_pause(self) -> None:
        store = Store()
        tab = SpotifyTab(store, mock.Mock(), self.fonts)
        clock = [100.0]
        with mock.patch.object(spotify.time, "monotonic", lambda: clock[0]):
            store.apply(media("First very long title that certainly does not fit in the space next to the art"))
            tab.draw(self.screen, pygame.Rect(0, 64, 800, 416), None)
            clock[0] += 10
            store.apply(media("Second very long title that certainly does not fit in the space next to the art"))
            tab.draw(self.screen, pygame.Rect(0, 64, 800, 416), None)
            self.assertEqual(tab._marquee_started, clock[0])


if __name__ == "__main__":
    unittest.main()
