"""Polarity tab (only while Polarity runs on the PC): what's playing like the Spotify tab, otherwise Continue
Watching and the library. Polarity itself isn't changed; the PC app reads its player and data file (docs §4)."""

from __future__ import annotations

import io
import time
from collections import OrderedDict

import pygame

from deck.net import NetClient
from deck.state import MediaState, Store
from deck.ui import glass, spotify, theme
from deck.ui.spotify import _format_time
from deck.ui.tab import Tab
from deck.ui.theme import Fonts
from deck.ui.widgets import Hit, centered_text, circle_hit, knob, media_icon, rounded

POSTER = (200, 300)  # now playing: centred in Spotify's 300×300 art slot
CARD = (120, 180)  # Continue Watching
SMALL = (80, 120)  # library
CARD_GAP = 16
SMALL_GAP = 12
POSTER_RADIUS = 14
MAX_POSTER_SURFACES = 48


class PolarityTab(Tab):
    title = "Polarity"

    def __init__(self, store: Store, net: NetClient, fonts: Fonts) -> None:
        self._store = store
        self._net = net
        self._fonts = fonts
        self._posters: OrderedDict[tuple[str, tuple[int, int]], pygame.Surface] = OrderedDict()
        self._backplate = glass.Backplate()
        self._scroll = {"continue": 0.0, "library": 0.0}
        self._max_scroll = {"continue": 0.0, "library": 0.0}
        self._scrolling: str | None = None

    # --- state ---------------------------------------------------------------------------------------------

    def visible(self) -> bool:
        return bool(self._store.polarity and self._store.polarity.get("running")) and self._store.shows("polarity")

    def _playing(self) -> MediaState | None:
        playing = (self._store.polarity or {}).get("playing")
        return MediaState.from_message({**playing, "available": True}) if playing else None

    def animating(self) -> bool:
        playing = self._playing()
        return playing is not None and playing.is_playing

    def animation_delay_s(self) -> float:
        playing = self._playing()
        if playing is None:
            return 1.0
        position = playing.position_at(int(time.time() * 1000))
        return (1000 - position % 1000) / 1000 + 0.005

    # --- input ---------------------------------------------------------------------------------------------

    def on_tap(self, key: str, pos: tuple[float, float]) -> None:
        if key == "play_pause":
            self._net.send({"type": "polarity.command", "action": key})

    def on_scroll_begin(self, key: str) -> None:
        self._scrolling = key.removeprefix("scroll:") if key.startswith("scroll:") else None

    def on_scroll(self, dx: float) -> None:
        if self._scrolling is not None:
            row = self._scrolling
            self._scroll[row] = min(max(self._scroll[row] - dx, 0.0), self._max_scroll[row])

    def on_scroll_end(self) -> None:
        self._scrolling = None

    # --- drawing -------------------------------------------------------------------------------------------

    def draw(self, surface: pygame.Surface, area: pygame.Rect, pressed: str | None) -> list[Hit]:
        playing = self._playing()
        if playing is not None:
            return self._draw_playing(surface, area, playing, pressed)
        return self._draw_browse(surface, area)

    def _draw_playing(self, surface: pygame.Surface, area: pygame.Rect, playing: MediaState, pressed: str | None) -> list[Hit]:
        """Laid out exactly like the Spotify tab (same slot, text column, sizes, progress bar and play button),
        so switching between the two doesn't shift anything; the 2:3 poster is centred in the 300×300 art slot."""
        raw = self._store.polarity["playing"]
        slot = pygame.Rect(area.left + spotify.MARGIN, area.top + (area.height - spotify.ART_SIZE) // 2,
                           spotify.ART_SIZE, spotify.ART_SIZE)
        poster_rect = pygame.Rect(0, 0, *POSTER)
        poster_rect.center = slot.center
        info = pygame.Rect(slot.right + spotify.MARGIN, slot.top, area.right - spotify.MARGIN - slot.right - spotify.MARGIN, spotify.ART_SIZE)
        play = (info.centerx, info.bottom - 40)

        poster = self._poster(raw.get("posterHash"), POSTER)
        panels = [] if poster is not None else [glass.panel(poster_rect, POSTER_RADIUS, glass.CARD)]
        style = glass.TINTED_PRESSED if pressed == "play_pause" else glass.TINTED
        panels.append(glass.circle_panel(play, 40, style, theme.ACCENT))
        self._backplate.draw(surface, area, theme.BACKGROUND, panels)

        f = self._fonts
        if poster is not None:
            surface.blit(poster, poster_rect)
        else:
            centered_text(surface, f.render((playing.title or "?")[:1].upper(), 96, theme.TEXT_FAINT, bold=True), poster_rect.center)

        title = f.render(raw.get("title") or "Playing", spotify.TITLE_SIZE, theme.TEXT, bold=True, max_width=info.width)
        surface.blit(title, info.topleft)
        if subtitle := raw.get("subtitle"):  # where Spotify shows the artist
            line = f.render(subtitle, 22, theme.TEXT_DIM, max_width=info.width)
            surface.blit(line, (info.left, info.top + title.get_height() + 6))

        self._draw_progress(surface, playing, pygame.Rect(info.left, info.top + 150, info.width, 8))
        media_icon(surface, "pause" if playing.is_playing else "play", play, 26, theme.BACKGROUND)
        return [circle_hit("play_pause", play, 40)]

    def _draw_progress(self, surface: pygame.Surface, playing: MediaState, bar: pygame.Rect) -> None:
        position = playing.position_at(int(time.time() * 1000))
        fraction = position / playing.duration_ms if playing.duration_ms > 0 else 0.0
        radius = bar.height // 2
        pygame.draw.rect(surface, theme.TRACK, bar, border_radius=radius)
        filled = pygame.Rect(bar.left, bar.top, round(bar.width * fraction), bar.height)
        if filled.width > 0:
            pygame.draw.rect(surface, theme.ACCENT, filled, border_radius=radius)
        knob(surface, (filled.right, bar.centery), 9, theme.ACCENT)
        f = self._fonts
        elapsed = f.render(_format_time(position), 16, theme.TEXT_DIM)
        total = f.render(_format_time(playing.duration_ms), 16, theme.TEXT_DIM)
        surface.blit(elapsed, (bar.left, bar.bottom + 12))
        surface.blit(total, (bar.right - total.get_width(), bar.bottom + 12))

    def _draw_browse(self, surface: pygame.Surface, area: pygame.Rect) -> list[Hit]:
        """Nothing playing: Continue Watching (big posters with progress), then the library (small posters)."""
        state = self._store.polarity or {}
        continue_cards, library = state.get("continue") or [], state.get("library") or []
        f = self._fonts
        if not continue_cards and not library:
            centered_text(surface, f.render("Nothing playing in Polarity", 28, theme.TEXT_DIM, bold=True), (area.centerx, area.centery - 16))
            centered_text(surface, f.render("Start something in Polarity on the PC", 18, theme.TEXT_FAINT), (area.centerx, area.centery + 22))
            return []

        hits = []
        top = area.top + 12
        surface.blit(f.render("Continue watching", 20, theme.TEXT, bold=True), (area.left + 24, top))
        row = pygame.Rect(area.left, top + 30, area.width, CARD[1] + 28)
        if continue_cards:
            hits.append(self._draw_row(surface, "continue", continue_cards, row, CARD, CARD_GAP, titles=True))
        else:
            surface.blit(f.render("Nothing to continue", 16, theme.TEXT_FAINT), (area.left + 24, row.top + 8))

        top = row.bottom + 8
        surface.blit(f.render("Library", 20, theme.TEXT, bold=True), (area.left + 24, top))
        row = pygame.Rect(area.left, top + 30, area.width, SMALL[1])
        if library:
            hits.append(self._draw_row(surface, "library", library, row, SMALL, SMALL_GAP, titles=False))
        else:
            surface.blit(f.render("Your library is empty", 16, theme.TEXT_FAINT), (area.left + 24, row.top + 8))
        return hits

    def _draw_row(self, surface: pygame.Surface, name: str, cards: list[dict], row: pygame.Rect,
                  size: tuple[int, int], gap: int, titles: bool) -> Hit:
        """One horizontally scrolling row of posters. Display only: Polarity can't be told what to play."""
        f = self._fonts
        pitch = size[0] + gap
        total = 24 + len(cards) * pitch - gap + 24
        self._max_scroll[name] = max(0.0, float(total - row.width))
        self._scroll[name] = min(self._scroll[name], self._max_scroll[name])
        left = row.left + 24 - round(self._scroll[name])

        clip = surface.get_clip()
        surface.set_clip(row)
        for index, card in enumerate(cards):
            x = left + index * pitch
            if x + size[0] < row.left or x > row.right:
                continue
            rect = pygame.Rect(x, row.top, *size)
            poster = self._poster(card.get("posterHash"), size)
            if poster is not None:
                surface.blit(poster, rect)
            else:
                pygame.draw.rect(surface, theme.SURFACE, rect, border_radius=POSTER_RADIUS - 4)
                label = f.render(card.get("title") or "?", 14, theme.TEXT_DIM, max_width=size[0] - 12)
                centered_text(surface, label, rect.center)
            if (progress := card.get("progress")) is not None:
                # Polarity-style progress strip along the poster's bottom edge.
                bar = pygame.Rect(rect.left + 8, rect.bottom - 12, rect.width - 16, 5)
                pygame.draw.rect(surface, theme.TEXT, bar, border_radius=3)
                fill = bar.copy()
                fill.width = max(5, round(bar.width * min(max(progress, 0.0), 1.0)))
                pygame.draw.rect(surface, theme.ACCENT, fill, border_radius=3)
            if titles:
                line = f.render(card.get("title") or "", 15, theme.TEXT, max_width=size[0])
                surface.blit(line, (rect.left, rect.bottom + 6))
        surface.set_clip(clip)
        return Hit(f"scroll:{name}", row, draggable=True)

    def _poster(self, poster_hash: str | None, size: tuple[int, int]) -> pygame.Surface | None:
        if poster_hash is None:
            return None
        key = (poster_hash, size)
        if (cached := self._posters.get(key)) is not None:
            self._posters.move_to_end(key)
            return cached
        data = self._store.posters.get(poster_hash)
        if data is None:
            return None  # polarity.art not received yet
        image = pygame.image.load(io.BytesIO(data), "poster.jpg").convert()
        image = _cover(image, size)
        radius = POSTER_RADIUS if size == POSTER else POSTER_RADIUS - 4
        image = rounded(image, radius, theme.BACKGROUND)
        self._posters[key] = image
        while len(self._posters) > MAX_POSTER_SURFACES:
            self._posters.popitem(last=False)
        return image


def _cover(image: pygame.Surface, size: tuple[int, int]) -> pygame.Surface:
    """Scales to fill `size`, cropping the overflow (posters are ~2:3, but not all exactly)."""
    width, height = image.get_size()
    scale = max(size[0] / width, size[1] / height)
    scaled = pygame.transform.smoothscale(image, (max(1, round(width * scale)), max(1, round(height * scale))))
    crop = pygame.Rect(0, 0, *size)
    crop.center = scaled.get_rect().center
    return scaled.subsurface(crop).copy()
