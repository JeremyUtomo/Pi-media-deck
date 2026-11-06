"""Base class for the screens behind the tab bar."""

from __future__ import annotations

import pygame

from deck.ui.widgets import Hit


class Tab:
    """Tabs draw immediate-mode and return the hit regions for this frame; the App routes input back by key.

    Gestures: a press that doesn't move is a tap. On a draggable hit, movement past a small threshold
    becomes a vertical drag (on_drag/on_drag_end) or a horizontal scroll (on_scroll/on_scroll_end).
    """

    title = ""
    needs_pc = True  # False for tabs that work without the PC connection (Lights)

    def animating(self) -> bool:
        """True while the tab needs periodic redraws (e.g. a playing progress bar)."""
        return False

    def animation_delay_s(self) -> float:
        """While animating(): how long until the next redraw is needed. Override to redraw exactly when
        something visibly changes (the Spotify tab: when the elapsed time ticks over)."""
        return 0.25

    def busy(self) -> bool:
        """True while the tab has time-critical work for tick() (e.g. a throttled command to flush)."""
        return False

    def tick(self) -> bool:
        """Called every loop iteration. Returns True if the tab needs a redraw."""
        return False

    def frame_interval_ms(self) -> int | None:
        """How often draw_frame() wants to run (e.g. a scrolling title), or None if nothing moves on its own."""
        return None

    def draw_frame(self, surface: pygame.Surface) -> list[pygame.Rect]:
        """Redraws just the self-moving parts on top of the last full draw; returns the rects to update."""
        return []

    def on_tap(self, key: str, pos: tuple[float, float]) -> None:
        pass

    def on_drag(self, key: str, pos: tuple[float, float]) -> None:
        pass

    def on_drag_end(self, key: str, pos: tuple[float, float]) -> None:
        pass

    def on_scroll(self, dx: float) -> None:
        pass

    def on_scroll_end(self) -> None:
        pass

    def draw(self, surface: pygame.Surface, area: pygame.Rect, pressed: str | None) -> list[Hit]:
        raise NotImplementedError
