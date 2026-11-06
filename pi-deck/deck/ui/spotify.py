"""Spotify tab: album art, track info, interpolated progress bar, transport buttons."""

from __future__ import annotations

import io
import time
from collections import OrderedDict

import pygame

from deck.net import NetClient
from deck.state import MediaState, Store
from deck.ui import glass, theme
from deck.ui.tab import Tab
from deck.ui.theme import Fonts
from deck.ui.widgets import Hit, centered_text, circle_hit, knob, media_icon, rounded

ART_SIZE = 300
MARGIN = 32
MAX_ART_SURFACES = 4
TITLE_SIZE = 32

# A title wider than its space scrolls: pause, scroll one full length plus a gap, repeat (seamless loop).
MARQUEE_PAUSE_S = 2.5
MARQUEE_SPEED = 40  # px/s
MARQUEE_GAP = 64
MARQUEE_FRAME_MS = 50  # only the title strip is redrawn at this rate
MARQUEE_FADE = 24


def _format_time(ms: int) -> str:
    seconds = ms // 1000
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02}:{rest % 60:02}" if hours else f"{rest // 60}:{rest % 60:02}"


class SpotifyTab(Tab):
    title = "Spotify"

    def __init__(self, store: Store, net: NetClient, fonts: Fonts) -> None:
        self._store = store
        self._net = net
        self._fonts = fonts
        self._art: OrderedDict[str, pygame.Surface] = OrderedDict()
        self._backplate = glass.Backplate()
        # Marquee state: the title strip on screen (None when the title fits), its text, and when it started.
        self._marquee_rect: pygame.Rect | None = None
        self._marquee_text: pygame.Surface | None = None
        self._marquee_title = ""
        self._marquee_started = 0.0
        self._fades: tuple[int, pygame.Surface, pygame.Surface] | None = None

    def visible(self) -> bool:
        return self._store.shows("spotify")

    def frame_interval_ms(self) -> int | None:
        return MARQUEE_FRAME_MS if self._marquee_rect is not None else None

    def draw_frame(self, surface: pygame.Surface) -> list[pygame.Rect]:
        if self._marquee_rect is None:
            return []
        self._draw_marquee(surface)
        return [self._marquee_rect]

    def animating(self) -> bool:
        media = self._store.media
        return media is not None and media.available and media.is_playing

    def animation_delay_s(self) -> float:
        """Redraw when the elapsed time (m:ss) next changes, i.e. once a second, just after the tick. The bar
        moves ~1–3 px per second, so this is smooth enough and far cheaper than redrawing continuously."""
        media = self._store.media
        if media is None:
            return 1.0
        position = media.position_at(int(time.time() * 1000))
        return (1000 - position % 1000) / 1000 + 0.005

    def on_tap(self, key: str, pos: tuple[float, float]) -> None:
        self._net.send({"type": "media.command", "action": key})

    def draw(self, surface: pygame.Surface, area: pygame.Rect, pressed: str | None) -> list[Hit]:
        self._marquee_rect = None
        media = self._store.media
        if media is None or not media.available:
            self._draw_idle(surface, area)
            return []

        art_rect = pygame.Rect(area.left + MARGIN, area.top + (area.height - ART_SIZE) // 2, ART_SIZE, ART_SIZE)
        info = pygame.Rect(art_rect.right + MARGIN, art_rect.top, area.right - MARGIN - art_rect.right - MARGIN, ART_SIZE)
        controls = (info.centerx, info.bottom - 40)

        # The glass (art placeholder, buttons) as one cached image: this tab redraws 4x/s while a track plays.
        panels = [] if self._art_surface(media.art_hash) is not None else [glass.panel(art_rect, theme.RADIUS_ART, glass.CARD)]
        panels += self._control_panels(controls, pressed)
        self._backplate.draw(surface, area, theme.BACKGROUND, panels)

        self._draw_art(surface, media.art_hash, art_rect)
        self._draw_text(surface, media, info)
        self._draw_progress(surface, media, pygame.Rect(info.left, info.top + 150, info.width, 8))
        return self._draw_controls(surface, media, controls, pressed)

    def _draw_idle(self, surface: pygame.Surface, area: pygame.Rect) -> None:
        f = self._fonts
        centered_text(surface, f.render("Spotify isn't playing", 28, theme.TEXT_DIM, bold=True), (area.centerx, area.centery - 16))
        centered_text(surface, f.render("Start Spotify on the PC", 18, theme.TEXT_FAINT), (area.centerx, area.centery + 22))

    def _draw_art(self, surface: pygame.Surface, art_hash: str | None, rect: pygame.Rect) -> None:
        art = self._art_surface(art_hash)
        if art is not None:
            surface.blit(art, rect)
            return
        centered_text(surface, self._fonts.render("♪", 96, theme.TEXT_FAINT), rect.center)

    def _art_surface(self, art_hash: str | None) -> pygame.Surface | None:
        if art_hash is None:
            return None
        if (cached := self._art.get(art_hash)) is not None:
            return cached
        data = self._store.art.get(art_hash)
        if data is None:
            return None  # media.art not received yet

        image = pygame.image.load(io.BytesIO(data), "art.jpg").convert()
        if image.get_size() != (ART_SIZE, ART_SIZE):
            image = pygame.transform.smoothscale(image, (ART_SIZE, ART_SIZE))
        image = rounded(image, theme.RADIUS_ART, theme.BACKGROUND)
        self._art[art_hash] = image
        while len(self._art) > MAX_ART_SURFACES:
            self._art.popitem(last=False)
        return image

    def _draw_text(self, surface: pygame.Surface, media: MediaState, info: pygame.Rect) -> None:
        f = self._fonts
        title = media.title or "Unknown title"
        rendered = f.render(title, TITLE_SIZE, theme.TEXT, bold=True)
        if rendered.get_width() <= info.width:
            surface.blit(rendered, info.topleft)
        else:
            if title != self._marquee_title:
                self._marquee_title = title
                self._marquee_started = time.monotonic()
            self._marquee_text = rendered
            self._marquee_rect = pygame.Rect(info.left, info.top, info.width, rendered.get_height())
            self._draw_marquee(surface)

        y = info.top + rendered.get_height() + 6
        for text, size, color, gap in (
            (media.artist, 22, theme.TEXT_DIM, 4),
            (media.album, 18, theme.TEXT_FAINT, 0),
        ):
            if not text:
                continue
            line = f.render(text, size, color, max_width=info.width)
            surface.blit(line, (info.left, y))
            y += line.get_height() + gap

    def _draw_marquee(self, surface: pygame.Surface) -> None:
        rect, text = self._marquee_rect, self._marquee_text
        assert rect is not None and text is not None
        distance = text.get_width() + MARQUEE_GAP
        elapsed = (time.monotonic() - self._marquee_started) % (MARQUEE_PAUSE_S + distance / MARQUEE_SPEED)
        offset = 0 if elapsed < MARQUEE_PAUSE_S else round((elapsed - MARQUEE_PAUSE_S) * MARQUEE_SPEED)

        clip = surface.get_clip()
        surface.set_clip(rect)
        surface.fill(theme.BACKGROUND, rect)
        surface.blit(text, (rect.left - offset, rect.top))
        surface.blit(text, (rect.left - offset + distance, rect.top))
        fade_left, fade_right = self._fade_surfaces(rect.height)
        if offset > 0:
            surface.blit(fade_left, rect.topleft)
        surface.blit(fade_right, (rect.right - MARQUEE_FADE, rect.top))
        surface.set_clip(clip)

    def _fade_surfaces(self, height: int) -> tuple[pygame.Surface, pygame.Surface]:
        """Background-colored gradients that soften the title where it enters and leaves its strip."""
        if self._fades is None or self._fades[0] != height:
            right = pygame.Surface((MARQUEE_FADE, height), pygame.SRCALPHA)
            for x in range(MARQUEE_FADE):
                alpha = round(255 * (x + 1) / MARQUEE_FADE)
                pygame.draw.line(right, (*theme.BACKGROUND, alpha), (x, 0), (x, height - 1))
            self._fades = (height, pygame.transform.flip(right, True, False), right)
        return self._fades[1], self._fades[2]

    def _draw_progress(self, surface: pygame.Surface, media: MediaState, bar: pygame.Rect) -> None:
        position = media.position_at(int(time.time() * 1000))
        fraction = position / media.duration_ms if media.duration_ms > 0 else 0.0

        radius = bar.height // 2
        pygame.draw.rect(surface, theme.TRACK, bar, border_radius=radius)
        filled = pygame.Rect(bar.left, bar.top, round(bar.width * fraction), bar.height)
        if filled.width > 0:
            pygame.draw.rect(surface, theme.ACCENT, filled, border_radius=radius)
        knob(surface, (filled.right, bar.centery), 9, theme.ACCENT)

        f = self._fonts
        elapsed = f.render(_format_time(position), 16, theme.TEXT_DIM)
        total = f.render(_format_time(media.duration_ms), 16, theme.TEXT_DIM)
        surface.blit(elapsed, (bar.left, bar.bottom + 12))
        surface.blit(total, (bar.right - total.get_width(), bar.bottom + 12))

    @staticmethod
    def _control_panels(center: tuple[int, int], pressed: str | None) -> list[glass.Panel]:
        cx, cy = center
        panels = [glass.circle_panel((x, cy), 32, glass.LENS_PRESSED if pressed == key else glass.LENS)
                  for key, x in (("previous", cx - 110), ("next", cx + 110))]
        style = glass.TINTED_PRESSED if pressed == "play_pause" else glass.TINTED
        panels.append(glass.circle_panel((cx, cy), 40, style, theme.ACCENT))
        return panels

    def _draw_controls(
        self, surface: pygame.Surface, media: MediaState, center: tuple[int, int], pressed: str | None
    ) -> list[Hit]:
        cx, cy = center
        hits = []

        for key, x, enabled in (("previous", cx - 110, media.can_previous), ("next", cx + 110, media.can_next)):
            media_icon(surface, key, (x, cy), 22, theme.TEXT if enabled else theme.TEXT_FAINT)
            if enabled:
                hits.append(circle_hit(key, (x, cy), 34))

        media_icon(surface, "pause" if media.is_playing else "play", (cx, cy), 26, theme.BACKGROUND)
        hits.append(circle_hit("play_pause", (cx, cy), 40))
        return hits
