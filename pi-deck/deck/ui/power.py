"""PC power button in the tab bar and the confirmation dialog in front of it."""

from __future__ import annotations

import pygame

from deck.net import NetClient
from deck.state import Store
from deck.ui import theme
from deck.ui.theme import Fonts
from deck.ui.widgets import Hit, centered_text, circle_hit, filled_circle, power_icon

BUTTON_KEY = "power:button"
CONFIRM_KEY = "power:confirm"
CANCEL_KEY = "power:cancel"
CARD_KEY = "power:card"  # the dialog body: absorbs taps so they don't cancel
DIALOG_SIZE = (440, 230)


class PowerControl:
    def __init__(self, store: Store, net: NetClient, fonts: Fonts) -> None:
        self._store = store
        self._net = net
        self._fonts = fonts
        self.open = False  # the confirmation dialog is showing

    def handles(self, key: str) -> bool:
        return key.startswith("power:")

    def on_tap(self, key: str) -> None:
        if key == BUTTON_KEY:
            self.open = True
        elif key == CONFIRM_KEY:
            self._net.send({"type": "pc.power", "action": "shutdown"})
            self.open = False
        elif key == CANCEL_KEY:
            self.open = False

    def draw_button(self, surface: pygame.Surface, center: tuple[int, int], pressed: str | None) -> list[Hit]:
        """Small neutral round button; only while the PC is connected (there's nothing to shut down otherwise)."""
        if not self._store.connected:
            return []
        filled_circle(surface, center, 18, theme.PRESSED if pressed == BUTTON_KEY or self.open else theme.SURFACE)
        power_icon(surface, center, 18, theme.TEXT_DIM)
        return [circle_hit(BUTTON_KEY, center, 18)]

    def draw_dialog(self, surface: pygame.Surface, pressed: str | None) -> list[Hit]:
        """The confirmation, over everything. Its hits replace all others; tapping outside cancels."""
        if not self.open:
            return []
        f = self._fonts
        shade = pygame.Surface(surface.get_size(), pygame.SRCALPHA)
        shade.fill((*theme.TEXT, 90))
        surface.blit(shade, (0, 0))

        card = pygame.Rect((0, 0), DIALOG_SIZE)
        card.center = surface.get_rect().center
        pygame.draw.rect(surface, theme.PRESSED, card.inflate(2, 2), border_radius=theme.RADIUS_CARD + 1)
        pygame.draw.rect(surface, theme.BACKGROUND, card, border_radius=theme.RADIUS_CARD)

        filled_circle(surface, (card.centerx, card.top + 42), 22, theme.MUTED)
        power_icon(surface, (card.centerx, card.top + 42), 22, theme.BACKGROUND)
        centered_text(surface, f.render("Shut down the PC?", 24, theme.TEXT, bold=True), (card.centerx, card.top + 92))
        centered_text(surface, f.render("Apps that are still open may lose unsaved work.", 15, theme.TEXT_DIM),
                      (card.centerx, card.top + 124))

        gap = 12
        width = (card.width - 48 - gap) // 2
        cancel = pygame.Rect(card.left + 24, card.bottom - 24 - 52, width, 52)
        confirm = pygame.Rect(cancel.right + gap, cancel.top, width, 52)
        pygame.draw.rect(surface, theme.PRESSED if pressed == CANCEL_KEY else theme.SURFACE_HIGH, cancel, border_radius=26)
        pygame.draw.rect(surface, theme.ACCENT_PRESSED if pressed == CONFIRM_KEY else theme.MUTED, confirm, border_radius=26)
        centered_text(surface, f.render("Cancel", 18, theme.TEXT, bold=True), cancel.center)
        centered_text(surface, f.render("Shut down", 18, theme.BACKGROUND, bold=True), confirm.center)

        # Hits are checked last-first: buttons, then the card itself (taps there do nothing), then outside = cancel.
        return [Hit(CANCEL_KEY, surface.get_rect()), Hit(CARD_KEY, card), Hit(CANCEL_KEY, cancel), Hit(CONFIRM_KEY, confirm)]
