"""What the idle (ambient) view shows: Spotify while it plays, else what's open in Polarity, else paused Spotify.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import unittest

from deck.state import Store
from deck.ui.ambient import choose


def spotify(playing: bool) -> dict:
    return {"type": "media.state", "available": True, "title": "Song", "artist": "Artist", "album": "Album",
            "durationMs": 1000, "positionMs": 0, "positionUpdatedAt": 0, "isPlaying": playing,
            "canPrevious": True, "canNext": True, "artHash": "cover"}


def polarity(playing: bool | None) -> dict:
    now = None if playing is None else {"title": "Show", "subtitle": "S1 E2", "posterHash": "poster",
                                        "durationMs": 1000, "positionMs": 0, "positionUpdatedAt": 0, "isPlaying": playing}
    return {"type": "polarity.state", "running": True, "playing": now, "continue": [], "library": []}


class ChooseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})

    def source(self) -> str | None:
        showing = choose(self.store)
        return None if showing is None else showing.source

    def test_nothing(self) -> None:
        self.assertIsNone(self.source())

    def test_spotify_playing_always_wins(self) -> None:
        self.store.apply(spotify(playing=True))
        self.store.apply(polarity(playing=True))
        self.assertEqual(self.source(), "spotify")

    def test_polarity_when_spotify_is_paused(self) -> None:
        self.store.apply(spotify(playing=False))
        self.store.apply(polarity(playing=True))
        showing = choose(self.store)
        self.assertEqual((showing.source, showing.title, showing.subtitle, showing.image_hash),
                         ("polarity", "Show", "S1 E2", "poster"))

    def test_polarity_paused_still_beats_paused_spotify(self) -> None:
        self.store.apply(spotify(playing=False))
        self.store.apply(polarity(playing=False))
        self.assertEqual(self.source(), "polarity")

    def test_polarity_when_spotify_is_off(self) -> None:
        self.store.apply(polarity(playing=True))
        self.assertEqual(self.source(), "polarity")

    def test_polarity_open_but_nothing_in_its_player(self) -> None:
        self.store.apply(spotify(playing=False))
        self.store.apply(polarity(playing=None))
        self.assertEqual(self.source(), "spotify", "falls back to the paused track")

    def test_settings_turn_sources_off(self) -> None:
        self.store.apply(spotify(playing=True))
        self.store.apply(polarity(playing=True))
        self.store.apply({"type": "deck.settings", "show": {"spotify": False}})
        self.assertEqual(self.source(), "polarity")
        self.store.apply({"type": "deck.settings", "show": {"spotify": False, "polarity": False}})
        self.assertIsNone(self.source())

    def test_pc_gone(self) -> None:
        self.store.apply(spotify(playing=True))
        self.store.apply({"type": "net.disconnected"})
        self.assertIsNone(self.source())


if __name__ == "__main__":
    unittest.main()
