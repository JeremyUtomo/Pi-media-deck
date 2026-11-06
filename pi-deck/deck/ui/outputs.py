"""Audio output pill in the tab bar and the popup to switch the PC's default playback device."""

from __future__ import annotations

import pygame

from deck.net import NetClient
from deck.state import Store
from deck.ui import glass, theme
from deck.ui.theme import Fonts
from deck.ui.widgets import Hit, filled_circle, filled_polygon, speaker_icon

MENU_KEY = "output:menu"
CLOSE_KEY = "output:close"
PILL_HEIGHT = 36
PILL_NAME_MAX = 80  # keep the tab bar uncluttered; the popup shows full names
MENU_WIDTH = 400
ROW_HEIGHT = 56
MENU_PADDING = 8


class OutputSelector:
    def __init__(self, store: Store, net: NetClient, fonts: Fonts) -> None:
        self._store = store
        self._net = net
        self._fonts = fonts
        self._menu_ids: list[str] = []  # output ids by row, as drawn
        self.open = False
        self.pill_left = theme.WIDTH  # left edge of the pill as last drawn (the quick light toggle sits next to it)

    def _outputs(self) -> tuple[str | None, list[dict]]:
        audio = self._store.audio or {}
        return audio.get("output"), audio.get("outputs") or []

    def handles(self, key: str) -> bool:
        return key.startswith("output:")

    def on_tap(self, key: str) -> None:
        if key == MENU_KEY:
            self.open = not self.open
        elif key == CLOSE_KEY:
            self.open = False
        else:
            index = int(key.removeprefix("output:"))
            current, _ = self._outputs()
            if index < len(self._menu_ids) and self._menu_ids[index] != current:
                self._net.send({"type": "audio.set_output", "id": self._menu_ids[index]})
            self.open = False

    def draw_pill(self, surface: pygame.Surface, right: int, center_y: int, pressed: str | None) -> list[Hit]:
        """The tab bar's right-hand pill: current output (tap to switch), or the connection state."""
        f = self._fonts
        current, outputs = self._outputs()
        name = next((o["name"] for o in outputs if o["id"] == current), None)

        if not self._store.connected or name is None:
            connected = self._store.connected
            label = f.render("PC" if connected else "Connecting…", 16, theme.TEXT_DIM)
            pill = pygame.Rect(0, 0, label.get_width() + 46, PILL_HEIGHT - 4)
            pill.midright = (right, center_y)
            glass.draw(surface, pill, pill.height // 2, glass.CHIP)
            filled_circle(surface, (pill.left + 18, center_y), 6, theme.CONNECTED if connected else theme.WARNING)
            surface.blit(label, label.get_rect(midleft=(pill.left + 32, center_y)))
            self.pill_left = pill.left
            return []

        label = f.render(split_name(name)[0], 16, theme.TEXT, max_width=PILL_NAME_MAX)
        pill = pygame.Rect(0, 0, label.get_width() + 58, PILL_HEIGHT)
        pill.midright = (right, center_y)
        style = glass.LENS_PRESSED if pressed == MENU_KEY else glass.LENS if self.open else glass.CHIP
        glass.draw(surface, pill, PILL_HEIGHT // 2, style)
        speaker_icon(surface, (pill.left + 18, center_y), 16, theme.ACCENT)
        surface.blit(label, label.get_rect(midleft=(pill.left + 32, center_y)))
        _chevron(surface, (pill.right - 14, center_y), theme.TEXT_DIM, up=self.open)
        self.pill_left = pill.left
        return [Hit(MENU_KEY, pill.inflate(12, 20))]

    def draw_menu(self, surface: pygame.Surface, pressed: str | None) -> list[Hit]:
        """The popup over everything, when open. Returns hits that replace all others while it's shown."""
        if not self.open:
            return []
        current, outputs = self._outputs()
        f = self._fonts

        shade = pygame.Surface((theme.WIDTH, theme.HEIGHT - theme.TAB_BAR_HEIGHT), pygame.SRCALPHA)
        shade.fill((*theme.TEXT, 70))
        surface.blit(shade, (0, theme.TAB_BAR_HEIGHT))

        max_rows = (theme.HEIGHT - theme.TAB_BAR_HEIGHT - 2 * MENU_PADDING - 8) // ROW_HEIGHT
        rows = outputs[:max_rows]
        panel = pygame.Rect(0, 0, MENU_WIDTH, 2 * MENU_PADDING + ROW_HEIGHT * max(len(rows), 1))
        panel.topright = (theme.WIDTH - 16, theme.TAB_BAR_HEIGHT + 2)
        glass.draw(surface, panel, theme.RADIUS_CARD, glass.POPUP)  # frosted: blurs the page behind it

        hits = [Hit(CLOSE_KEY, surface.get_rect())]
        self._menu_ids = [o["id"] for o in rows]
        for index, output in enumerate(rows):
            row = pygame.Rect(panel.left + MENU_PADDING, panel.top + MENU_PADDING + index * ROW_HEIGHT,
                              panel.width - 2 * MENU_PADDING, ROW_HEIGHT)
            key = f"output:{index}"
            selected = output["id"] == current
            if pressed == key or selected:
                glass.draw(surface, row, 14, glass.LENS_PRESSED if pressed == key else glass.LENS)
            if selected:
                _check(surface, (row.left + 24, row.centery), theme.ACCENT)
            endpoint, device = split_name(output["name"])
            label = f.render(endpoint, 18, theme.TEXT if selected else theme.TEXT_DIM, bold=selected,
                             max_width=row.width - 60)
            if device:
                sub = f.render(device, 14, theme.TEXT_FAINT, max_width=row.width - 60)
                surface.blit(label, (row.left + 48, row.centery - label.get_height() + 2))
                surface.blit(sub, (row.left + 48, row.centery + 2))
            else:
                surface.blit(label, label.get_rect(midleft=(row.left + 48, row.centery)))
            hits.append(Hit(key, row))
        if not rows:
            label = f.render("No playback devices", 18, theme.TEXT_FAINT)
            surface.blit(label, label.get_rect(center=panel.center))
        return hits


def split_name(name: str) -> tuple[str, str | None]:
    """Windows names outputs "<endpoint> (<device>)", e.g. "Speakers (Realtek(R) Audio)" -> ("Speakers", "Realtek(R) Audio")."""
    index = name.find(" (")
    if index > 0 and name.endswith(")"):
        return name[:index], name[index + 2 : -1]
    return name, None


def _chevron(surface: pygame.Surface, center: tuple[int, int], color, up: bool) -> None:
    x, y = center
    tip, base = (y - 4, y + 3) if up else (y + 4, y - 3)
    filled_polygon(surface, [(x - 6, base), (x + 6, base), (x, tip)], color)


def _check(surface: pygame.Surface, center: tuple[int, int], color) -> None:
    x, y = center
    pygame.draw.lines(surface, color, False, [(x - 9, y), (x - 3, y + 6), (x + 9, y - 7)], 4)
