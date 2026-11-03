"""Drawing helpers and hit regions shared by the tabs."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pygame
from pygame import gfxdraw

from deck.ui import theme


@dataclass(frozen=True)
class Hit:
    """A tappable region registered while drawing. `key` is passed back to the tab's handlers."""

    key: str
    rect: pygame.Rect
    draggable: bool = False
    horizontal: bool = False  # a horizontal slider: sideways movement drags it instead of scrolling the tab


def circle_hit(key: str, center: tuple[float, float], radius: int) -> Hit:
    size = 2 * (radius + theme.TOUCH_SLOP)
    rect = pygame.Rect(0, 0, size, size)
    rect.center = (round(center[0]), round(center[1]))
    return Hit(key, rect)


def filled_circle(surface: pygame.Surface, center: tuple[float, float], radius: int, color) -> None:
    x, y = round(center[0]), round(center[1])
    gfxdraw.aacircle(surface, x, y, radius, color)
    gfxdraw.filled_circle(surface, x, y, radius, color)


def knob(surface: pygame.Surface, center: tuple[float, float], radius: int, ring) -> None:
    """A cream slider knob with a colored ring."""
    filled_circle(surface, center, radius, ring)
    filled_circle(surface, center, radius - 3, theme.KNOB)


def filled_polygon(surface: pygame.Surface, points: list[tuple[float, float]], color) -> None:
    rounded = [(round(x), round(y)) for x, y in points]
    gfxdraw.aapolygon(surface, rounded, color)
    gfxdraw.filled_polygon(surface, rounded, color)


def media_icon(surface: pygame.Surface, kind: str, center: tuple[float, float], size: float, color) -> None:
    """Draws play / pause / previous / next glyphs fitting a `size`-px square around `center`."""
    cx, cy = center
    half = size / 2
    if kind == "play":
        # Nudged right so the triangle looks optically centered.
        filled_polygon(surface, [(cx - half * 0.6, cy - half), (cx - half * 0.6, cy + half), (cx + half, cy)], color)
    elif kind == "pause":
        bar_w, gap = size * 0.28, size * 0.16
        for x in (cx - gap / 2 - bar_w, cx + gap / 2):
            pygame.draw.rect(surface, color, pygame.Rect(round(x), round(cy - half), round(bar_w), round(size)), border_radius=2)
    elif kind in ("previous", "next"):
        bar_w = size * 0.14
        direction = -1 if kind == "previous" else 1
        tip = cx + direction * (half - bar_w)
        base = cx - direction * half
        filled_polygon(surface, [(base, cy - half), (base, cy + half), (tip, cy)], color)
        bar_x = cx + half - bar_w if kind == "next" else cx - half
        pygame.draw.rect(surface, color, pygame.Rect(round(bar_x), round(cy - half), round(bar_w), round(size)))
    else:
        raise ValueError(kind)


def bulb_icon(surface: pygame.Surface, center: tuple[float, float], size: float, color) -> None:
    """A light bulb fitting a `size`-px square around `center`."""
    cx, cy = center
    filled_circle(surface, (cx, cy - size * 0.12), round(size * 0.3), color)
    neck = pygame.Rect(0, 0, round(size * 0.3), round(size * 0.2))
    neck.midtop = (round(cx), round(cy + size * 0.1))
    pygame.draw.rect(surface, color, neck, border_radius=2)
    base = pygame.Rect(0, 0, round(size * 0.22), max(2, round(size * 0.08)))
    base.midtop = (round(cx), neck.bottom + max(1, round(size * 0.04)))
    pygame.draw.rect(surface, color, base, border_radius=2)


def power_icon(surface: pygame.Surface, center: tuple[float, float], size: float, color) -> None:
    """The standard power symbol: an open ring with a bar through the gap at the top."""
    cx, cy = center
    width = max(2, round(size / 9))
    ring = pygame.Rect(0, 0, round(size * 0.8), round(size * 0.8))
    ring.center = (round(cx), round(cy + size * 0.06))
    pygame.draw.arc(surface, color, ring, math.radians(120), math.radians(420), width)  # gap of 60° at the top
    pygame.draw.line(surface, color, (cx, cy - size * 0.48), (cx, cy - size * 0.02), width)


def _slash(surface: pygame.Surface, center: tuple[float, float], size: float, color) -> None:
    cx, cy = center
    half = size * 0.48
    pygame.draw.line(surface, color, (cx - half, cy - half), (cx + half, cy + half), max(3, round(size / 11)))


def mic_icon(surface: pygame.Surface, center: tuple[float, float], size: float, color, slashed: bool = False) -> None:
    """A microphone (Discord mute), crossed out when `slashed`."""
    cx, cy = center
    width = max(3, round(size / 12))
    capsule = pygame.Rect(0, 0, round(size * 0.34), round(size * 0.52))
    capsule.midtop = (round(cx), round(cy - size * 0.5))
    pygame.draw.rect(surface, color, capsule, border_radius=capsule.width // 2)
    cup = pygame.Rect(0, 0, round(size * 0.6), round(size * 0.6))
    cup.center = (round(cx), round(cy - size * 0.08))
    pygame.draw.arc(surface, color, cup, math.pi, 2 * math.pi, width)
    pygame.draw.line(surface, color, (cx, cup.bottom - 1), (cx, cy + size * 0.38), width)
    pygame.draw.line(surface, color, (cx - size * 0.18, cy + size * 0.38), (cx + size * 0.18, cy + size * 0.38), width)
    if slashed:
        _slash(surface, center, size, color)


def headphones_icon(surface: pygame.Surface, center: tuple[float, float], size: float, color, slashed: bool = False) -> None:
    """Headphones (Discord deafen), crossed out when `slashed`."""
    cx, cy = center
    width = max(3, round(size / 11))
    band = pygame.Rect(0, 0, round(size * 0.84), round(size * 0.84))
    band.center = (round(cx), round(cy + size * 0.02))
    pygame.draw.arc(surface, color, band, 0, math.pi, width)
    for side in (-1, 1):
        cup = pygame.Rect(0, 0, round(size * 0.22), round(size * 0.36))
        cup.center = (round(cx + side * size * 0.33), round(cy + size * 0.2))
        pygame.draw.rect(surface, color, cup, border_radius=round(size * 0.08))
    if slashed:
        _slash(surface, center, size, color)


def speaker_icon(surface: pygame.Surface, center: tuple[float, float], size: float, color, muted: bool = False) -> None:
    """A speaker with sound waves, or with an X when muted, fitting a `size`-px square around `center`."""
    half = size / 2
    cx, cy = center[0] - half * 0.25, center[1]
    cone_x = cx + half * 0.05
    filled_polygon(surface, [
        (cx - half * 0.75, cy - size * 0.17),
        (cx - half * 0.35, cy - size * 0.17),
        (cone_x, cy - half * 0.8),
        (cone_x, cy + half * 0.8),
        (cx - half * 0.35, cy + size * 0.17),
        (cx - half * 0.75, cy + size * 0.17),
    ], color)

    width = max(2, round(size / 14))
    if muted:
        left, right, reach = cone_x + half * 0.3, cone_x + half * 0.95, half * 0.32
        pygame.draw.line(surface, color, (left, cy - reach), (right, cy + reach), width)
        pygame.draw.line(surface, color, (left, cy + reach), (right, cy - reach), width)
        return
    for radius in (half * 0.45, half * 0.85):
        box = pygame.Rect(0, 0, round(2 * radius), round(2 * radius))
        box.center = (round(cone_x), round(cy))
        pygame.draw.arc(surface, color, box, -math.pi / 4, math.pi / 4, width)


def rounded(image: pygame.Surface, radius: int, background: tuple[int, int, int] | None = None) -> pygame.Surface:
    """A copy of `image` with anti-aliased rounded corners.

    With `background`, the result is composited onto that color and opaque, which is cheaper to blit every
    frame; without it, it keeps per-pixel alpha (for drawing over pictures).
    """
    width, height = image.get_size()
    # Draw the mask at 2x and scale down: pygame's rounded rects aren't anti-aliased.
    big = pygame.Surface((width * 2, height * 2), pygame.SRCALPHA)
    pygame.draw.rect(big, (255, 255, 255, 255), big.get_rect(), border_radius=radius * 2)
    mask = pygame.transform.smoothscale(big, (width, height))
    result = image.convert_alpha()
    result.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    if background is None:
        return result
    opaque = pygame.Surface((width, height)).convert()
    opaque.fill(background)
    opaque.blit(result, (0, 0))
    return opaque


def centered_text(surface: pygame.Surface, text_surface: pygame.Surface, center: tuple[float, float]) -> None:
    surface.blit(text_surface, text_surface.get_rect(center=(round(center[0]), round(center[1]))))
