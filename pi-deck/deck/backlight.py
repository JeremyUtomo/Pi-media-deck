"""Switches the screen's backlight on and off (the HyperPixel 4's is a GPIO: on/off only, no dimming)."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

BACKLIGHT_DIR = Path("/sys/class/backlight")


class Backlight:
    """Writes /sys/class/backlight/*/brightness (group `video`). A no-op where there is none, e.g. on the PC."""

    def __init__(self, root: Path = BACKLIGHT_DIR) -> None:
        device = next(iter(sorted(root.glob("*"))), None) if root.is_dir() else None
        self._brightness = device / "brightness" if device else None
        self._max = "1"
        if device is not None:
            try:
                self._max = (device / "max_brightness").read_text().strip() or "1"
            except OSError:
                pass
        self.on: bool | None = None  # unknown until the first set(): a crashed run may have left it off

    def set(self, on: bool) -> None:
        if on == self.on:
            return
        self.on = on
        if self._brightness is None:
            return
        try:
            self._brightness.write_text(self._max if on else "0")
            log.info("backlight %s", "on" if on else "off")
        except OSError as error:
            log.warning("can't switch the backlight: %s", error)
