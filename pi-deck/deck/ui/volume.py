"""Volume tab: a mixer strip for master plus one per app — icon, name, vertical slider, percentage, mute."""

from __future__ import annotations

import io
import math
import time
from dataclasses import dataclass

import pygame

from deck.net import NetClient
from deck.state import Store
from deck.ui import glass, theme
from deck.ui.tab import Tab
from deck.ui.theme import Fonts
from deck.ui.widgets import Hit, centered_text, headphones_icon, knob, mic_icon, speaker_icon

MASTER_ID = "master"
STRIP_WIDTH = 132
ICON_SIZE = 48
# Offsets from the top of the content area.
ICON_CENTER_Y = 40
NAME_CENTER_Y = 84
TRACK_TOP = 108
TRACK_HEIGHT = 192
TRACK_WIDTH = 10
MUTE_SIZE = (88, 36)
CARD_GAP_X = 5  # each strip is a rounded card inset this much from its 132 px slot
CARD_GAP_Y = 8
SIDEBAR_WIDTH = 176  # Discord mute/deafen card, shown while in a voice call

SEND_INTERVAL_S = 0.05  # at most one set_volume per 50 ms while dragging
OVERRIDE_HOLD_S = 1.0  # keep showing the dragged value briefly after release, until the PC's echo catches up


@dataclass(frozen=True)
class Strip:
    id: str
    name: str
    volume: float
    muted: bool
    icon_hash: str | None


class VolumeTab(Tab):
    title = "Volume"

    def __init__(self, store: Store, net: NetClient, fonts: Fonts) -> None:
        self._store = store
        self._net = net
        self._fonts = fonts
        self._icons: dict[str, pygame.Surface] = {}
        self._backplate = glass.Backplate()
        self._scroll = 0.0
        self._max_scroll = 0.0
        self._area = pygame.Rect(0, theme.TAB_BAR_HEIGHT, theme.WIDTH, theme.HEIGHT - theme.TAB_BAR_HEIGHT)
        # Locally dragged volumes shown instead of the PC's, by target id: (volume, show until monotonic time).
        self._overrides: dict[str, tuple[float, float]] = {}
        self._pending: tuple[str, float] | None = None
        self._last_send = 0.0

    # --- input ---------------------------------------------------------------------------------------------

    def on_tap(self, key: str, pos: tuple[float, float]) -> None:
        kind, _, target = key.partition(":")
        if kind == "vol":
            self._set_volume(target, self._volume_at(pos[1]), final=True)
        elif kind == "mute":
            strip = next((s for s in self._strips() if s.id == target), None)
            if strip is not None:
                self._net.send({"type": "audio.set_mute", "target": target, "muted": not strip.muted})
        elif kind == "discord":
            # The PC flips Discord's current state, so quick double taps can't disagree with Discord.
            self._net.send({"type": "discord.command", "action": "toggle_mute" if target == "mute" else "toggle_deafen"})

    def on_drag(self, key: str, pos: tuple[float, float]) -> None:
        kind, _, target = key.partition(":")
        if kind == "vol":
            self._set_volume(target, self._volume_at(pos[1]), final=False)

    def on_drag_end(self, key: str, pos: tuple[float, float]) -> None:
        kind, _, target = key.partition(":")
        if kind == "vol":
            self._set_volume(target, self._volume_at(pos[1]), final=True)

    def on_scroll(self, dx: float) -> None:
        self._scroll = min(max(self._scroll - dx, 0.0), self._max_scroll)

    def on_scroll_end(self) -> None:
        self._scroll = min(round(self._scroll / STRIP_WIDTH) * STRIP_WIDTH, self._max_scroll)

    def busy(self) -> bool:
        return self._pending is not None

    def tick(self) -> bool:
        now = time.monotonic()
        if self._pending is not None and now - self._last_send >= SEND_INTERVAL_S:
            self._send_volume(*self._pending, now)
        expired = [target for target, (_, until) in self._overrides.items() if now >= until]
        for target in expired:
            del self._overrides[target]
        return bool(expired)

    def _volume_at(self, y: float) -> float:
        track_bottom = self._area.top + TRACK_TOP + TRACK_HEIGHT
        return min(max((track_bottom - y) / TRACK_HEIGHT, 0.0), 1.0)

    def _set_volume(self, target: str, volume: float, final: bool) -> None:
        now = time.monotonic()
        self._overrides[target] = (volume, now + OVERRIDE_HOLD_S if final else math.inf)
        if final or now - self._last_send >= SEND_INTERVAL_S:
            self._send_volume(target, volume, now)
        else:
            self._pending = (target, volume)

    def _send_volume(self, target: str, volume: float, now: float) -> None:
        self._net.send({"type": "audio.set_volume", "target": target, "volume": round(volume, 3)})
        self._last_send = now
        self._pending = None

    # --- drawing -------------------------------------------------------------------------------------------

    def _strips(self) -> list[Strip]:
        audio = self._store.audio
        if audio is None:
            return []
        master = audio["master"]
        strips = [Strip(MASTER_ID, "Master", master["volume"], master["muted"], None)]
        strips += [
            Strip(s["id"], s["name"], s["volume"], s["muted"], s.get("iconHash")) for s in audio.get("sessions", [])
        ]
        return strips

    def draw(self, surface: pygame.Surface, area: pygame.Rect, pressed: str | None) -> list[Hit]:
        self._area = full = area
        sidebar: pygame.Rect | None = None
        discord = self._store.discord or {}
        if discord.get("status") == "ready" and discord.get("inCall"):
            # In a Discord call: a sidebar with mute/deafen on the right; the mixer gets the rest (and scrolls).
            sidebar = pygame.Rect(area.right - 16 - SIDEBAR_WIDTH, area.top + 8, SIDEBAR_WIDTH, area.height - 16)
            area = pygame.Rect(area.left, area.top, sidebar.left - 8 - area.left, area.height)

        strips = self._strips()
        total = len(strips) * STRIP_WIDTH
        self._max_scroll = max(0.0, float(total - area.width))
        self._scroll = min(self._scroll, self._max_scroll)
        left = area.left + (area.width - total) // 2 if total <= area.width else area.left - round(self._scroll)
        visible = []
        for index, strip in enumerate(strips):
            rect = pygame.Rect(left + index * STRIP_WIDTH, area.top, STRIP_WIDTH, area.height)
            if rect.right >= area.left and rect.left <= area.right:
                visible.append((strip, rect))

        # All glass first, as one cached image (unchanged while a slider is dragged), then the content on top.
        panels = self._discord_panels(sidebar, discord, pressed) if sidebar else []
        for strip, rect in visible:
            panels += self._strip_panels(strip, rect, pressed, area)
        self._backplate.draw(surface, full, theme.BACKGROUND, panels)

        sidebar_hits = self._draw_discord(surface, sidebar, discord, pressed) if sidebar else []
        if not strips:
            centered_text(surface, self._fonts.render("No audio info yet", 22, theme.TEXT_FAINT), area.center)
            return sidebar_hits

        # Lowest-priority hit: lets a horizontal swipe anywhere in the mixer scroll it.
        hits = [Hit("scroll", area, draggable=True)]
        now = time.monotonic()
        clip = surface.get_clip()
        surface.set_clip(area)
        for strip, rect in visible:
            hits += self._draw_strip(surface, strip, rect, pressed, now)
        surface.set_clip(clip)

        if self._max_scroll > 0:
            self._draw_scrollbar(surface, area, total)
        return [Hit(h.key, h.rect.clip(area), h.draggable) for h in hits] + sidebar_hits

    def _discord_buttons(self, card: pygame.Rect, discord: dict):
        buttons = (
            ("discord:mute", "Mute", "Muted", bool(discord.get("muted")), mic_icon),
            ("discord:deafen", "Deafen", "Deafened", bool(discord.get("deafened")), headphones_icon),
        )
        height = (card.bottom - 12 - (card.top + 124) - 10) // 2
        for index, (key, label, active_label, active, icon_fn) in enumerate(buttons):
            button = pygame.Rect(card.left + 12, card.top + 124 + index * (height + 10), card.width - 24, height)
            yield key, label, active_label, active, icon_fn, button

    def _discord_icon(self) -> pygame.Surface | None:
        session = next((s for s in (self._store.audio or {}).get("sessions", []) if s["id"] == "discord.exe"), None)
        return self._icon_surface(session.get("iconHash") if session else None)

    def _discord_panels(self, card: pygame.Rect, discord: dict, pressed: str | None) -> list[glass.Panel]:
        panels = [glass.panel(card, theme.RADIUS_CARD, glass.CARD)]
        if self._discord_icon() is None:
            panels.append(glass.circle_panel((card.centerx, card.top + 40), ICON_SIZE // 2, glass.LENS))
        for key, _, _, active, _, button in self._discord_buttons(card, discord):
            if active:
                panels.append(glass.panel(button, 18, glass.TINTED_PRESSED if pressed == key else glass.TINTED, theme.MUTED))
            else:
                panels.append(glass.panel(button, 18, glass.LENS_PRESSED if pressed == key else glass.LENS))
        return panels

    def _draw_discord(self, surface: pygame.Surface, card: pygame.Rect, discord: dict, pressed: str | None) -> list[Hit]:
        f = self._fonts
        icon = self._discord_icon()
        center = (card.centerx, card.top + 40)
        if icon is not None:
            surface.blit(icon, icon.get_rect(center=center))
        else:
            centered_text(surface, f.render("D", 22, theme.TEXT_DIM, bold=True), center)
        centered_text(surface, f.render("In call", 14, theme.TEXT_FAINT), (card.centerx, card.top + 80))
        # No emoji font on the Pi: drop emoji (common in channel names) rather than show empty boxes.
        name = " ".join("".join(c for c in (discord.get("channel") or "") if ord(c) < 0x1F000 and c != "️").split())
        channel = f.render(name or "Voice", 16, theme.TEXT, bold=True, max_width=card.width - 20)
        centered_text(surface, channel, (card.centerx, card.top + 100))

        hits = []
        for key, label, active_label, active, icon_fn, button in self._discord_buttons(card, discord):
            ink = theme.BACKGROUND if active else theme.TEXT
            icon_fn(surface, (button.centerx, button.centery - 14), 40, ink, slashed=active)
            text = f.render(active_label if active else label, 17, ink, bold=active)
            centered_text(surface, text, (button.centerx, button.bottom - 22))
            hits.append(Hit(key, button))
        return hits

    def _draw_strip(self, surface: pygame.Surface, strip: Strip, rect: pygame.Rect, pressed: str | None, now: float) -> list[Hit]:
        f = self._fonts
        cx = rect.centerx
        volume_key, mute_key = f"vol:{strip.id}", f"mute:{strip.id}"
        override = self._overrides.get(strip.id)
        volume = override[0] if override is not None and now < override[1] else strip.volume

        card = rect.inflate(-2 * CARD_GAP_X, -2 * CARD_GAP_Y)
        self._draw_icon(surface, strip, (cx, rect.top + ICON_CENTER_Y))
        name = f.render(strip.name, 16, theme.TEXT if strip.id == MASTER_ID else theme.TEXT_DIM,
                        bold=strip.id == MASTER_ID, max_width=rect.width - 16)
        centered_text(surface, name, (cx, rect.top + NAME_CENTER_Y))

        track = pygame.Rect(0, 0, TRACK_WIDTH, TRACK_HEIGHT)
        track.midtop = (cx, rect.top + TRACK_TOP)
        pygame.draw.rect(surface, theme.TRACK, track, border_radius=TRACK_WIDTH // 2)
        fill_height = round(TRACK_HEIGHT * volume)
        if fill_height > 0:
            fill = pygame.Rect(track.left, track.bottom - fill_height, TRACK_WIDTH, fill_height)
            pygame.draw.rect(surface, theme.TEXT_FAINT if strip.muted else theme.ACCENT, fill, border_radius=TRACK_WIDTH // 2)
        knob(surface, (cx, track.bottom - fill_height), 15 if pressed == volume_key else 12,
             theme.TEXT_FAINT if strip.muted else theme.ACCENT)

        percent = f.render(f"{round(volume * 100)}%", 20, theme.TEXT_FAINT if strip.muted else theme.TEXT, bold=True)
        centered_text(surface, percent, (cx, track.bottom + 22))

        button = self._mute_button(card)
        speaker_icon(surface, button.center, 22, theme.BACKGROUND if strip.muted else theme.TEXT, muted=strip.muted)

        slider_hit = pygame.Rect(rect.left, track.top - 20, rect.width, track.height + 40)
        return [Hit(volume_key, slider_hit, draggable=True), Hit(mute_key, button.inflate(12, 12))]

    @staticmethod
    def _mute_button(card: pygame.Rect) -> pygame.Rect:
        button = pygame.Rect((0, 0), MUTE_SIZE)
        button.center = (card.centerx, card.bottom - 10 - MUTE_SIZE[1] // 2)
        return button

    def _strip_panels(self, strip: Strip, rect: pygame.Rect, pressed: str | None, clip: pygame.Rect) -> list[glass.Panel]:
        """The strip's glass: card, icon circle (master, or no app icon), mute button."""
        card = rect.inflate(-2 * CARD_GAP_X, -2 * CARD_GAP_Y)
        mute_key = f"mute:{strip.id}"
        panels = [glass.panel(card, theme.RADIUS_CARD, glass.CARD, clip=clip)]
        center = (rect.centerx, rect.top + ICON_CENTER_Y)
        if strip.id == MASTER_ID:
            panels.append(glass.circle_panel(center, ICON_SIZE // 2, glass.TINTED, theme.ACCENT, clip=clip))
        elif self._icon_surface(strip.icon_hash) is None:
            panels.append(glass.circle_panel(center, ICON_SIZE // 2, glass.LENS, clip=clip))
        button = self._mute_button(card)
        if strip.muted:
            style = glass.TINTED_PRESSED if pressed == mute_key else glass.TINTED
            panels.append(glass.panel(button, MUTE_SIZE[1] // 2, style, theme.MUTED, clip=clip))
        else:
            style = glass.LENS_PRESSED if pressed == mute_key else glass.LENS
            panels.append(glass.panel(button, MUTE_SIZE[1] // 2, style, clip=clip))
        return panels

    def _draw_icon(self, surface: pygame.Surface, strip: Strip, center: tuple[int, int]) -> None:
        if strip.id == MASTER_ID:
            speaker_icon(surface, center, 26, theme.BACKGROUND)
            return
        icon = self._icon_surface(strip.icon_hash)
        if icon is not None:
            surface.blit(icon, icon.get_rect(center=center))
            return
        # No icon (system sounds, elevated apps): first letter in a circle.
        centered_text(surface, self._fonts.render(strip.name[:1].upper() or "?", 22, theme.TEXT_DIM, bold=True), center)

    def _icon_surface(self, icon_hash: str | None) -> pygame.Surface | None:
        if icon_hash is None:
            return None
        if (cached := self._icons.get(icon_hash)) is not None:
            return cached
        data = self._store.icons.get(icon_hash)
        if data is None:
            return None
        icon = pygame.image.load(io.BytesIO(data), "icon.png").convert_alpha()
        if icon.get_size() != (ICON_SIZE, ICON_SIZE):
            icon = pygame.transform.smoothscale(icon, (ICON_SIZE, ICON_SIZE))
        self._icons[icon_hash] = icon
        return icon

    def _draw_scrollbar(self, surface: pygame.Surface, area: pygame.Rect, total: int) -> None:
        track = pygame.Rect(area.left + 40, area.bottom - 4, area.width - 80, 3)
        thumb_width = max(24, round(track.width * area.width / total))
        thumb_left = track.left + round((track.width - thumb_width) * self._scroll / self._max_scroll)
        pygame.draw.rect(surface, theme.TRACK, track, border_radius=2)
        pygame.draw.rect(surface, theme.TEXT_FAINT, (thumb_left, track.top, thumb_width, track.height), border_radius=2)
