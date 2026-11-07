"""Ambient view shown after a while without touch: the current album cover (or Polarity poster), dimmed, over a
blurred copy of it.

The HyperPixel backlight is on/off only, so "lower brightness" is done by darkening the image itself.
"""

from __future__ import annotations

import io
import time
from dataclasses import dataclass

import pygame

from deck.state import Store
from deck.ui import theme
from deck.ui.theme import Fonts
from deck.ui.widgets import centered_text, rounded

COVER_SIZE = 300  # the PC sends art at 300x300 (Spotify's thumbnail size); larger looks pixelated
POSTER_SIZE = (200, 300)  # Polarity posters, as the PC sends them
COVER_TOP = 80
CAPTION_Y = 418
CLOCK_Y = 42  # centre of the clock above the cover
CLOCK_SIZE = 40
CLOCK_ALONE_SIZE = 120  # nothing playing: the clock fills the screen
BACKGROUND_DIM = 0.35  # relative to the cover's brightness
BLUR_SIZE = 64  # the background is the cover shrunk to this and scaled back up: a cheap blur


@dataclass(frozen=True)
class Showing:
    """What the ambient view shows: Spotify's track or what's open in Polarity's player."""

    source: str  # "spotify" | "polarity"
    title: str
    subtitle: str  # artist, or the episode
    image_hash: str | None


def choose(store: Store) -> Showing | None:
    """Spotify while it plays, always. Otherwise what's open in Polarity's player (playing or paused), then a paused
    Spotify track. None: nothing to show (just the clock). Parts turned off in the PC app's settings don't count."""
    if not store.connected:
        return None
    media = store.media
    spotify = None
    if media is not None and media.available and store.shows("spotify"):
        spotify = Showing("spotify", media.title, media.artist, media.art_hash)
        if media.is_playing:
            return spotify
    polarity = store.polarity or {}
    playing = polarity.get("playing")
    if polarity.get("running") and playing and store.shows("polarity"):
        return Showing("polarity", playing.get("title") or "", playing.get("subtitle") or "", playing.get("posterHash"))
    return spotify


class AmbientView:
    def __init__(self, store: Store, fonts: Fonts, brightness: float) -> None:
        self._store = store
        self._fonts = fonts
        self._brightness = min(max(brightness, 0.05), 1.0)
        # (source, image hash, background, cover) for the last image shown
        self._layers: tuple[str, str, pygame.Surface, pygame.Surface] | None = None
        self._shown_minute = ""

    def minute_changed(self) -> bool:
        """True once per minute, so the App knows to redraw the clock."""
        minute = time.strftime("%H:%M")
        if minute != self._shown_minute:
            self._shown_minute = minute
            return True
        return False

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill((0, 0, 0))
        showing = choose(self._store)
        now = time.strftime("%H:%M")
        self._shown_minute = now
        if showing is None:
            # Nothing playing: just a big, calm clock.
            clock = self._fonts.render(now, CLOCK_ALONE_SIZE, self._dim(theme.AMBIENT_TEXT, 0.85), bold=True)
            centered_text(surface, clock, surface.get_rect().center)
            return

        layers = self._layers_for(showing)
        if layers is not None:
            background, cover = layers
            surface.blit(background, (0, 0))
            surface.blit(cover, cover.get_rect(midtop=(theme.WIDTH // 2, COVER_TOP)))
        else:
            note = self._fonts.render("♪" if showing.source == "spotify" else "▶", 96, self._dim(theme.AMBIENT_TEXT, 0.45))
            centered_text(surface, note, (theme.WIDTH // 2, COVER_TOP + COVER_SIZE // 2))

        # The clock above the cover (drawn after the blurred background).
        clock = self._fonts.render(now, CLOCK_SIZE, self._dim(theme.AMBIENT_TEXT, 0.9), bold=True)
        centered_text(surface, clock, (theme.WIDTH // 2, CLOCK_Y))

        caption = " — ".join(part for part in (showing.title, showing.subtitle) if part)
        if caption:
            text = self._fonts.render(caption, 18, self._dim(theme.AMBIENT_TEXT, 0.8), max_width=theme.WIDTH - 80)
            centered_text(surface, text, (theme.WIDTH // 2, CAPTION_Y))

    def _layers_for(self, showing: Showing) -> tuple[pygame.Surface, pygame.Surface] | None:
        if showing.image_hash is None:
            return None
        if self._layers is not None and self._layers[:2] == (showing.source, showing.image_hash):
            return self._layers[2], self._layers[3]
        images = self._store.art if showing.source == "spotify" else self._store.posters
        data = images.get(showing.image_hash)
        if data is None:
            return None

        image = pygame.image.load(io.BytesIO(data), "art.jpg").convert()
        # Album covers are square; Polarity posters 2:3. Both are COVER_SIZE tall (the PC sends them at that size).
        size = (COVER_SIZE, COVER_SIZE) if showing.source == "spotify" else POSTER_SIZE
        cover = image.copy() if image.get_size() == size else pygame.transform.smoothscale(image, size)
        self._multiply(cover, self._brightness)
        cover = rounded(cover, theme.RADIUS_ART + 4)  # alpha corners: it's drawn over the blurred background

        # Fill the screen: shrink (a cheap blur), crop the middle to the screen's aspect ratio, scale it back up.
        width, height = image.get_size()
        small = pygame.transform.smoothscale(image, (BLUR_SIZE, max(1, round(BLUR_SIZE * height / width))))
        crop_height = min(small.get_height(), round(BLUR_SIZE * theme.HEIGHT / theme.WIDTH))
        crop = small.subsurface((0, (small.get_height() - crop_height) // 2, BLUR_SIZE, crop_height))
        background = pygame.transform.smoothscale(crop, (theme.WIDTH, theme.HEIGHT))
        self._multiply(background, self._brightness * BACKGROUND_DIM)

        self._layers = (showing.source, showing.image_hash, background, cover)
        return background, cover

    @staticmethod
    def _multiply(surface: pygame.Surface, factor: float) -> None:
        level = round(255 * factor)
        surface.fill((level, level, level), special_flags=pygame.BLEND_MULT)

    def _dim(self, color: tuple[int, int, int], factor: float = 1.0) -> tuple[int, int, int]:
        return tuple(round(c * self._brightness * factor) for c in color)  # type: ignore[return-value]
