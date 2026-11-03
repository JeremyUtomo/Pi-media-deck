"""Tests for the GT911 poller's pure logic: point parsing, coordinate transform, multitouch events.

Run from pi-deck/: python -m unittest discover tests
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "touchd"))

import gt911_touchd as td  # noqa: E402

# The HyperPixel 4 device tree: 480x800 portrait panel, inverted-y, swapped-x-y.
HYPERPIXEL = td.Geometry(size_x=480, size_y=800, invert_y=True, swap_x_y=True, x_mm=51, y_mm=85)


def record(track: int, x: int, y: int) -> bytes:
    return bytes([track, x & 0xFF, x >> 8, y & 0xFF, y >> 8, 20, 0, 0])


class TouchdTests(unittest.TestCase):
    def test_parse_points(self) -> None:
        data = record(0, 100, 700) + record(3, 479, 0)
        self.assertEqual(td.parse_points(2, data), {0: (100, 700), 3: (479, 0)})
        self.assertEqual(td.parse_points(2, data[:8]), {0: (100, 700)}, "short data is truncated, not misread")

    def test_transform_matches_kernel_touchscreen_helpers(self) -> None:
        # invert y (799 - y), then swap: reported (x, y) = (799 - raw_y, raw_x).
        self.assertEqual(HYPERPIXEL.transform(100, 700), (99, 100))
        self.assertEqual(HYPERPIXEL.transform(0, 0), (799, 0))
        self.assertEqual(HYPERPIXEL.axes(), ((799, 9), (479, 9)))

    def test_touch_down_move_up(self) -> None:
        contacts = td.Contacts()
        down = contacts.update({0: (10, 20)})
        self.assertIn((td.EV_ABS, td.ABS_MT_TRACKING_ID, 0), down)
        self.assertIn((td.EV_KEY, td.BTN_TOUCH, 1), down)
        self.assertEqual(down[-1], (td.EV_SYN, td.SYN_REPORT, 0))

        move = contacts.update({0: (15, 25)})
        self.assertNotIn(td.ABS_MT_TRACKING_ID, [code for _, code, _ in move], "same contact keeps its id")
        self.assertIn((td.EV_ABS, td.ABS_MT_POSITION_X, 15), move)

        up = contacts.update({})
        self.assertIn((td.EV_ABS, td.ABS_MT_TRACKING_ID, -1), up)
        self.assertIn((td.EV_KEY, td.BTN_TOUCH, 0), up)
        self.assertEqual(contacts.update({}), [], "no events when nothing changes")

    def test_new_contact_gets_new_tracking_id(self) -> None:
        contacts = td.Contacts()
        contacts.update({0: (1, 1)})
        contacts.update({})
        again = contacts.update({0: (2, 2)})
        self.assertIn((td.EV_ABS, td.ABS_MT_TRACKING_ID, 1), again)


if __name__ == "__main__":
    unittest.main()
