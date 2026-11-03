"""Tests for the Discord sidebar in the Volume tab.

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
from deck.ui import theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402


class FakeNet:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send(self, message: dict) -> None:
        self.sent.append(message)


def discord(status: str = "ready", in_call: bool = True, muted: bool = False, deafened: bool = False) -> dict:
    return {"type": "discord.state", "status": status, "inCall": in_call, "channel": "General",
            "muted": muted, "deafened": deafened}


class DiscordSidebarTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.store.apply({"type": "audio.state", "master": {"volume": 0.5, "muted": False}, "sessions": [
            {"id": f"app{i}.exe", "name": f"App {i}", "volume": 1.0, "muted": False, "iconHash": None} for i in range(4)]})
        self.net = FakeNet()
        self.app = App(self.screen, self.store, self.net, Fonts())  # type: ignore[arg-type]
        self.app.select_tab("volume")

    def tearDown(self) -> None:
        pygame.quit()

    def keys(self) -> list[str]:
        self.app.draw()
        return [h.key for h in self.app._hits]

    def tap(self, key: str) -> None:
        rect = next(h.rect for h in self.app._hits if h.key == key)
        self.app.pointer_down(rect.center)
        self.app.pointer_up(rect.center)

    def test_sidebar_only_while_in_a_call(self) -> None:
        self.assertNotIn("discord:mute", self.keys())
        self.store.apply(discord(in_call=False))
        self.assertNotIn("discord:mute", self.keys())
        self.store.apply(discord(status="needs_setup"))
        self.assertNotIn("discord:mute", self.keys())
        self.store.apply(discord())
        self.assertIn("discord:mute", self.keys())
        self.assertIn("discord:deafen", self.keys())

    def test_buttons_send_toggles(self) -> None:
        self.store.apply(discord())
        self.keys()
        self.tap("discord:mute")
        self.tap("discord:deafen")
        self.assertEqual(self.net.sent, [{"type": "discord.command", "action": "toggle_mute"},
                                         {"type": "discord.command", "action": "toggle_deafen"}])

    def test_mixer_makes_room_and_still_works(self) -> None:
        self.store.apply(discord())
        self.keys()
        sidebar_left = next(h.rect for h in self.app._hits if h.key == "discord:mute").left
        strips = [h.rect for h in self.app._hits if h.key.startswith("vol:")]
        self.assertTrue(strips)
        self.assertTrue(all(rect.right <= sidebar_left for rect in strips), "strips stay left of the sidebar")
        self.tap("vol:master")
        self.assertEqual(self.net.sent[-1]["type"], "audio.set_volume")


if __name__ == "__main__":
    unittest.main()
