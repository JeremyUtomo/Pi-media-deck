"""Idle timer for the ambient view (dimmed album cover) shown after a while without touch."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

log = logging.getLogger(__name__)


class IdleTimer:
    def __init__(self, idle_after_s: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._idle_after_s = idle_after_s
        self._clock = clock
        self._last_activity = clock()
        self.idle = False

    def activity(self) -> bool:
        """Records a touch. Returns True if it ended idle mode, in which case the touch should be ignored."""
        self._last_activity = self._clock()
        if self.idle:
            log.info("leaving ambient view (touch)")
            self.idle = False
            return True
        return False

    def update(self) -> bool:
        """Call regularly. Returns True if idle mode just started (needs a redraw)."""
        if self.idle or self._idle_after_s <= 0:
            return False
        if self._clock() - self._last_activity >= self._idle_after_s:
            log.info("ambient view after %.0f s without touch", self._idle_after_s)
            self.idle = True
            return True
        return False
