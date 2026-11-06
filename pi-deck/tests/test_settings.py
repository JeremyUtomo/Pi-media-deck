"""Tests for deck.settings: the PC app's "Deck settings" hide tabs and buttons on the deck.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck.app import App  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import theme  # noqa: E402
from deck.ui.lights import QUICK_KEY  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402

LAMP = {"id": "1", "name": "Lamp", "reachable": True, "on": True, "level": 50, "temperature": 2700,
        "can_temperature": True, "can_color": False, "warmest": 2202, "coolest": 6535}


def settings(**show: bool) -> dict:
    return {"type": "deck.settings", "show": show}


class SettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.store.apply({"type": "audio.state", "master": {"volume": 0.5, "muted": False}, "sessions": [],
                          "output": "a", "outputs": [{"id": "a", "name": "Speakers (Device)"}]})
        self.store.apply({"type": "lights.state", "lamps": [LAMP]})
        self.app = App(self.screen, self.store, mock.Mock(), Fonts(), lights=mock.Mock())
        self.redraw()

    def tearDown(self) -> None:
        pygame.quit()

    def redraw(self) -> None:
        self.app.tick()
        self.app.dirty = True
        self.app.draw()

    def keys(self) -> list[str]:
        return [h.key for h in self.app._hits]

    def tabs(self) -> list[str]:
        return [self.app._tabs[int(k.removeprefix("tab:"))].title for k in self.keys() if k.startswith("tab:")]

    def test_everything_on_by_default(self) -> None:
        self.assertEqual(self.tabs(), ["Volume", "Spotify", "Lights"])
        for key in ("power:button", QUICK_KEY, "output:menu"):
            self.assertIn(key, self.keys())

    def test_hiding_spotify_falls_back_to_volume(self) -> None:
        self.store.apply(settings(spotify=False))
        self.redraw()
        self.assertEqual(self.tabs(), ["Volume", "Lights"])
        self.assertEqual(self.app._tab.title, "Volume")

    def test_hiding_lights_removes_tab_and_button(self) -> None:
        self.store.apply(settings(lights=False))
        self.redraw()
        self.assertNotIn("Lights", self.tabs())
        self.assertNotIn(QUICK_KEY, self.keys())

    def test_hiding_power_and_outputs(self) -> None:
        self.app._outputs.open = True
        self.store.apply(settings(power=False, outputs=False))
        self.redraw()
        self.assertFalse(self.app._outputs.open, "an open output menu closes")
        self.assertNotIn("power:button", self.keys())
        self.assertNotIn("output:menu", self.keys(), "the pill only shows the connection")

    def test_hiding_polarity(self) -> None:
        self.store.apply({"type": "polarity.state", "running": True, "playing": None, "continue": [], "library": []})
        self.redraw()
        self.assertIn("Polarity", self.tabs())
        self.store.apply(settings(polarity=False))
        self.redraw()
        self.assertNotIn("Polarity", self.tabs())

    def test_hiding_discord_sidebar(self) -> None:
        self.store.apply({"type": "discord.state", "status": "ready", "inCall": True, "channel": "General",
                          "muted": False, "deafened": False})
        self.app.select_tab("volume")
        self.redraw()
        self.assertIn("discord:mute", self.keys())
        self.store.apply(settings(discord=False))
        self.redraw()
        self.assertNotIn("discord:mute", self.keys())

    def test_unknown_keys_and_missing_ones_stay_on(self) -> None:
        self.store.apply(settings(future_thing=False))
        self.assertTrue(self.store.shows("spotify"))
        self.assertFalse(self.store.shows("future_thing"))


if __name__ == "__main__":
    unittest.main()
