"""PC power button in the tab bar and the confirmation dialog in front of it."""

from __future__ import annotations

import pygame

from deck.net import NetClient
from deck.state import Store
from deck.ui import glass, theme
from deck.ui.theme import Fonts
from deck.ui.widgets import Hit, centered_text, circle_hit, power_icon

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
        glass.circle(surface, center, 18, glass.LENS_PRESSED if pressed == BUTTON_KEY or self.open else glass.CHIP)
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
        glass.draw(surface, card, theme.RADIUS_CARD, glass.POPUP)  # frosted: blurs the page behind it

        glass.circle(surface, (card.centerx, card.top + 42), 22, glass.TINTED, theme.MUTED)
        power_icon(surface, (card.centerx, card.top + 42), 22, theme.BACKGROUND)
        centered_text(surface, f.render("Shut down the PC?", 24, theme.TEXT, bold=True), (card.centerx, card.top + 92))
        centered_text(surface, f.render("Apps that are still open may lose unsaved work.", 15, theme.TEXT_DIM),
                      (card.centerx, card.top + 124))

        gap = 12
        width = (card.width - 48 - gap) // 2
        cancel = pygame.Rect(card.left + 24, card.bottom - 24 - 52, width, 52)
        confirm = pygame.Rect(cancel.right + gap, cancel.top, width, 52)
        glass.draw(surface, cancel, 26, glass.LENS_PRESSED if pressed == CANCEL_KEY else glass.LENS)
        glass.draw(surface, confirm, 26, glass.TINTED_PRESSED if pressed == CONFIRM_KEY else glass.TINTED, theme.MUTED)
        centered_text(surface, f.render("Cancel", 18, theme.TEXT, bold=True), cancel.center)
        centered_text(surface, f.render("Shut down", 18, theme.BACKGROUND, bold=True), confirm.center)

        # Hits are checked last-first: buttons, then the card itself (taps there do nothing), then outside = cancel.
        return [Hit(CANCEL_KEY, surface.get_rect()), Hit(CARD_KEY, card), Hit(CANCEL_KEY, cancel), Hit(CONFIRM_KEY, confirm)]
