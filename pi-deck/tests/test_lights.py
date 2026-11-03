"""Tests for the DIRIGERA client logic (parsing, events, command coalescing) and the Lights tab / quick toggle.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import asyncio
import os
import queue
import time
import unittest
from pathlib import Path

os.environ["SDL_VIDEODRIVER"] = "dummy"
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402

from deck import lights  # noqa: E402
from deck.app import App  # noqa: E402
from deck.state import Store  # noqa: E402
from deck.ui import lights as lights_ui  # noqa: E402
from deck.ui import theme  # noqa: E402
from deck.ui.theme import Fonts  # noqa: E402


def kajplats(index: int, on: bool = True, level: int = 57) -> dict:
    """A DIRIGERA /v1/devices entry shaped like the real KAJPLATS GU10 WS bulbs."""
    return {
        "id": f"lamp-{index}_1",
        "deviceType": "light",
        "isReachable": True,
        "room": {"name": "Bedroom"},
        "capabilities": {"canReceive": ["isOn", "lightLevel", "colorTemperature", "customName"]},
        "attributes": {"customName": f"KAJPLATS {index}", "isOn": on, "lightLevel": level, "colorTemperature": 2624,
                       "colorTemperatureMin": 6535, "colorTemperatureMax": 2202},
    }


class FakeLights:
    def __init__(self) -> None:
        self.sent: list[tuple[list[str], dict]] = []

    def set(self, lamp_ids: list[str], **attributes) -> None:
        self.sent.append((list(lamp_ids), attributes))


class FakeNet:
    def send(self, message: dict) -> None:
        pass


class ClientLogicTests(unittest.TestCase):
    def test_parse_lamp_unswaps_temperature_range(self) -> None:
        lamp = lights.parse_lamp(kajplats(1))
        self.assertEqual((lamp["warmest"], lamp["coolest"]), (2202, 6535))
        self.assertTrue(lamp["can_temperature"])
        self.assertFalse(lamp["can_color"], "WS bulbs are white spectrum, no hue")
        self.assertEqual(lamp["name"], "KAJPLATS 1")

    def test_events_update_lamps_and_post_state(self) -> None:
        inbox: queue.Queue[dict] = queue.Queue()
        client = lights.LightsClient("bedroom", inbox, lambda: None, config=Path("missing.json"))
        client._lamps = {"lamp-1_1": lights.parse_lamp(kajplats(1))}
        client._handle_event({"type": "deviceStateChanged", "data": {"id": "lamp-1_1", "attributes": {"isOn": False}}})
        client._handle_event({"type": "deviceStateChanged", "data": {"id": "someone-elses", "attributes": {"isOn": True}}})
        message = inbox.get_nowait()
        self.assertEqual(message["type"], lights.LIGHTS_STATE)
        self.assertFalse(message["lamps"][0]["on"])
        self.assertTrue(inbox.empty(), "events for other rooms' lights are ignored")

    def test_send_loop_coalesces_and_switches_on_first(self) -> None:
        client = lights.LightsClient("bedroom", queue.Queue(), lambda: None, config=Path("missing.json"))
        requests: list[tuple[str, str, object]] = []
        client._request = lambda method, path, body=None: requests.append((method, path, body))  # type: ignore[method-assign]

        async def scenario() -> None:
            client._commands = asyncio.Queue()
            for level in (10, 20, 30):
                client._commands.put_nowait(("lamp-1_1", {"isOn": True, "lightLevel": level}))
            task = asyncio.create_task(client._send_loop())
            await asyncio.sleep(0.2)
            task.cancel()

        asyncio.run(scenario())
        self.assertEqual(requests, [
            ("PATCH", "/devices/lamp-1_1", [{"attributes": {"isOn": True}}]),
            ("PATCH", "/devices/lamp-1_1", [{"attributes": {"lightLevel": 30}}]),
        ])


class ParallelSendTests(unittest.TestCase):
    def test_all_lamps_switch_together_and_on_comes_first(self) -> None:
        client = lights.LightsClient("bedroom", queue.Queue(), lambda: None, config=Path("missing.json"))
        calls: list[tuple[float, str, dict]] = []

        def slow_request(method: str, path: str, body: object = None) -> None:
            calls.append((time.monotonic(), path, body[0]["attributes"]))  # type: ignore[index]
            time.sleep(0.2)  # like a hub round trip

        client._request = slow_request  # type: ignore[method-assign]

        async def scenario() -> None:
            client._commands = asyncio.Queue()
            for index in (1, 2, 3):
                client._commands.put_nowait((f"lamp-{index}_1", {"isOn": True, "lightLevel": 40}))
            task = asyncio.create_task(client._send_loop())
            await asyncio.sleep(0.7)
            task.cancel()

        asyncio.run(scenario())
        on_calls = [c for c in calls if "isOn" in c[2]]
        level_calls = [c for c in calls if "lightLevel" in c[2]]
        self.assertEqual(len(on_calls), 3)
        self.assertEqual(len(level_calls), 3)
        self.assertLess(max(t for t, _, _ in on_calls) - min(t for t, _, _ in on_calls), 0.1,
                        "the three lamps are sent in parallel, not one after another")
        self.assertGreaterEqual(min(t for t, _, _ in level_calls), max(t for t, _, _ in on_calls),
                                "brightness follows only after every lamp got switched on")


class LightsUiTests(unittest.TestCase):
    def setUp(self) -> None:
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT))
        self.store = Store()
        self.store.apply({"type": "net.connected", "endpoint": "test"})
        self.store.apply({"type": lights.LIGHTS_STATE, "lamps": [lights.parse_lamp(kajplats(i)) for i in (1, 2, 3)]})
        self.lights = FakeLights()
        self.app = App(self.screen, self.store, FakeNet(), Fonts(), lights=self.lights, lights_title="Ceiling lights")  # type: ignore[arg-type]
        self.app.select_tab("lights")
        self.app.draw()
        self.ids = ["lamp-1_1", "lamp-2_1", "lamp-3_1"]

    def tearDown(self) -> None:
        pygame.quit()

    def hit(self, key: str) -> pygame.Rect:
        return next(h.rect for h in self.app._hits if h.key == key)

    def tap(self, pos: tuple[float, float]) -> None:
        self.app.pointer_down(pos)
        self.app.pointer_up(pos)
        self.app.draw()

    def test_power_turns_everything_off_when_any_is_on(self) -> None:
        self.tap(self.hit("light:power").center)
        self.assertEqual(self.lights.sent, [(self.ids, {"isOn": False})])

    def test_brightness_drag_is_throttled_and_ends_on_release_value(self) -> None:
        track = self.hit("light:level")
        y = track.centery
        self.app.pointer_down((track.left + 30, y))
        for step in range(1, 15):
            self.app.pointer_move((track.left + 30 + step * 20, y))
        self.app.pointer_up((track.right, y))
        self.assertLess(len(self.lights.sent), 5, "moves inside 150 ms are coalesced")
        ids, attributes = self.lights.sent[-1]
        self.assertEqual(ids, self.ids)
        self.assertEqual(attributes, {"isOn": True, "lightLevel": 100})

    def test_preset_sets_warmth_and_switches_on(self) -> None:
        self.tap(self.hit("light:preset:2202").center)
        self.assertEqual(self.lights.sent, [(self.ids, {"isOn": True, "colorTemperature": 2202})])

    def test_brightness_preset_sets_all_lamps(self) -> None:
        self.tap(self.hit("light:level:75").center)
        self.assertEqual(self.lights.sent, [(self.ids, {"isOn": True, "lightLevel": 75})])

    def test_no_per_lamp_controls(self) -> None:
        self.assertFalse([h for h in self.app._hits if h.key.startswith("light:lamp:")], "the lamps always go together")

    def test_quick_toggle_from_another_tab(self) -> None:
        self.app.select_tab("spotify")
        self.app.draw()
        self.tap(self.hit(lights_ui.QUICK_KEY).center)
        self.assertEqual(self.lights.sent, [(self.ids, {"isOn": False})])

    def test_lights_tab_works_without_the_pc(self) -> None:
        self.store.apply({"type": "net.disconnected"})
        self.app.draw()
        self.assertIn("light:power", [h.key for h in self.app._hits])

    def test_unavailable_hub_hides_quick_toggle(self) -> None:
        self.store.apply({"type": lights.LIGHTS_UNAVAILABLE})
        self.app.draw()
        self.assertNotIn(lights_ui.QUICK_KEY, [h.key for h in self.app._hits])


if __name__ == "__main__":
    unittest.main()
