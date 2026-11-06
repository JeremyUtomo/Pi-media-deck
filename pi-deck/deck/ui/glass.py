"""Liquid-glass panels: frosted, softly shadowed shapes with a light rim, used for the whole UI.

Every static layer (shadow, body tint, rim) is rendered once per size and style and cached. Panels without a
backdrop blur (cards, buttons) are one cached blit; blurred ones (tab bar, popups) add a cheap blur of what's
behind them (two smoothscales) — fine on the Pi Zero.
"""

from __future__ import annotations

from dataclasses import dataclass

import pygame

# Masks and rims are drawn supersampled and scaled down (pygame's rounded rects aren't anti-aliased): 4x for
# shapes up to 200 px (2x left big circles fuzzy), 2x for big cards, whose small corners don't need more
# and whose 4x canvases took up to a second to build on the Pi Zero.
def _scale(size: tuple[int, int]) -> int:
    return 4 if max(size) <= 200 else 2


@dataclass(frozen=True)
class GlassStyle:
    fill: tuple[int, int, int, int]  # tint over the blurred backdrop
    highlight: int  # extra white alpha at the top edge, fading out by the middle
    rim_top: int  # rim alpha at the top…
    rim_bottom: int  # …and at the bottom
    shadow: int  # drop shadow alpha (0 = none)
    shadow_offset: int = 3
    shadow_blur: int = 8
    blur: int = 10  # backdrop downscale factor (bigger = blurrier); 0 = no backdrop blur


# The bar itself: frosted, airy, clearly lifted off the page.
BAR = GlassStyle(fill=(255, 250, 240, 92), highlight=70, rim_top=210, rim_bottom=70, shadow=60)
# The selected tab inside the bar: a brighter lens.
LENS = GlassStyle(fill=(255, 252, 246, 150), highlight=80, rim_top=235, rim_bottom=110, shadow=34,
                  shadow_offset=2, shadow_blur=5, blur=0)
LENS_PRESSED = GlassStyle(fill=(236, 224, 206, 170), highlight=30, rim_top=180, rim_bottom=90, shadow=20,
                          shadow_offset=1, shadow_blur=4, blur=0)
# Small buttons in the bar (power, light, output): a faint chip so they read as tappable.
CHIP = GlassStyle(fill=(255, 252, 246, 70), highlight=50, rim_top=170, rim_bottom=60, shadow=0, blur=0)

# Cards: frosted panels a touch lighter than the page, lifted by a soft shadow. No backdrop blur (the page
# behind them is flat), so each is one cached blit.
CARD = GlassStyle(fill=(255, 250, 242, 78), highlight=42, rim_top=205, rim_bottom=60, shadow=42,
                  shadow_offset=4, shadow_blur=10, blur=0)
# Popups float over content, so they really blur what's behind them.
POPUP = GlassStyle(fill=(248, 239, 226, 200), highlight=45, rim_top=235, rim_bottom=90, shadow=80,
                   shadow_offset=6, shadow_blur=14, blur=14)
# Over a solid colour (play button, selected preset, lights on, active mute): just the gloss and the rim.
TINTED = GlassStyle(fill=(255, 255, 255, 0), highlight=62, rim_top=150, rim_bottom=40, shadow=36,
                    shadow_offset=2, shadow_blur=5, blur=0)
TINTED_PRESSED = GlassStyle(fill=(40, 20, 10, 30), highlight=20, rim_top=110, rim_bottom=30, shadow=18,
                            shadow_offset=1, shadow_blur=4, blur=0)

MAX_CACHED = 400

SHADOW_COLOR = (64, 44, 32)  # theme.TEXT, cocoa: a warm shadow instead of grey
# Transparent white, not (0, 0, 0, 0): smoothscale averages colour too, so black transparent pixels would
# leave a grey fringe on every anti-aliased edge.
CLEAR = (255, 255, 255, 0)

_cache: dict[tuple, pygame.Surface] = {}


def draw(surface: pygame.Surface, rect: pygame.Rect, radius: int, style: GlassStyle,
         color: tuple[int, int, int] | None = None) -> None:
    """Draws a glass capsule over whatever is already on `surface` inside `rect`.

    With `color`, it's tinted glass: a solid fill in that colour under the highlight and rim (play button,
    selected presets…). Without a backdrop blur, all layers are pre-composited into one cached image, so a card
    costs a single blit — the Volume tab redraws all its strips on every slider move.
    """
    rect = pygame.Rect(rect)
    size, radius = rect.size, max(0, min(radius, rect.height // 2, rect.width // 2))
    pad = style.shadow_blur * 2 if style.shadow else 0

    if style.blur <= 0:
        panel = _layer("panel", size, radius, style, color)  # premultiplied alpha
        surface.blit(panel, (rect.left - pad, rect.top - pad), special_flags=pygame.BLEND_PREMULTIPLIED)
        return

    backdrop = None
    if rect.clip(surface.get_rect()).size == rect.size:
        backdrop = _blurred(surface.subsurface(rect), style.blur)
        backdrop.blit(_layer("mask", size, radius), (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    if style.shadow:
        surface.blit(_layer("shadow", size, radius, style), (rect.left - pad, rect.top - pad + style.shadow_offset))
    if backdrop is not None:
        surface.blit(backdrop, rect.topleft)
    if color is not None:
        surface.blit(_layer("solid", size, radius, None, color), rect.topleft)
    surface.blit(_layer("body", size, radius, style), rect.topleft)
    surface.blit(_layer("rim", size, radius, style), rect.topleft)


# (rect, radius, style, color, clip): one glass shape for a Backplate; clip limits where it may draw.
Panel = tuple


def panel(rect: pygame.Rect, radius: int, style: GlassStyle, color: tuple[int, int, int] | None = None,
          clip: pygame.Rect | None = None) -> Panel:
    return (tuple(pygame.Rect(rect)), radius, style, color, None if clip is None else tuple(pygame.Rect(clip)))


def circle_panel(center: tuple[float, float], radius: int, style: GlassStyle,
                 color: tuple[int, int, int] | None = None, clip: pygame.Rect | None = None) -> Panel:
    rect = pygame.Rect(0, 0, 2 * radius, 2 * radius)
    rect.center = (round(center[0]), round(center[1]))
    return panel(rect, radius, style, color, clip)


class Backplate:
    """A screen area's static glass (cards, buttons) composited once into one opaque image over a flat colour.

    Glass panels are alpha blits, the bulk of a redraw on the Pi Zero (the Volume tab went from 10 to 31 ms).
    A tab lists its panels each frame; while that list is unchanged (e.g. during a slider drag) the redraw is
    one opaque blit plus whatever moves on top.
    """

    def __init__(self) -> None:
        self._key: tuple | None = None
        self._image: pygame.Surface | None = None

    def draw(self, surface: pygame.Surface, area: pygame.Rect, background: tuple[int, int, int],
             panels: list[Panel]) -> None:
        area = pygame.Rect(area)
        key = (tuple(area), background, tuple(panels))
        if key != self._key or self._image is None:
            image = pygame.Surface(area.size).convert()
            image.fill(background)
            for rect, radius, style, color, clip in panels:
                image.set_clip(None if clip is None else pygame.Rect(clip).move(-area.left, -area.top))
                draw(image, pygame.Rect(rect).move(-area.left, -area.top), radius, style, color)
            image.set_clip(None)
            dither(image, area.topleft)
            self._key, self._image = key, image
        surface.blit(self._image, area.topleft)


# 4x4 ordered (Bayer) dither, 0–3 per channel. The HyperPixel's DPI link is 18-bit (6 bits per colour), so the
# panel shows only every 4th shade. The glass gradients span ~5 shades, which became visible bands. Adding
# 0–3 in this pattern before the panel drops the low 2 bits keeps the in-between shades (as a fine,
# invisible-at-233-ppi texture).
_BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
_dither_pattern: pygame.Surface | None = None


def dither(surface: pygame.Surface, origin: tuple[int, int] = (0, 0)) -> None:
    """Adds the dither pattern to an opaque surface, aligned to screen coordinates (`origin` = its top-left
    on screen), so neighbouring areas tile seamlessly."""
    global _dither_pattern
    width, height = surface.get_size()
    if _dither_pattern is None or _dither_pattern.get_width() < width + 4 or _dither_pattern.get_height() < height + 4:
        tile = pygame.Surface((4, 4))
        for y, row in enumerate(_BAYER):
            for x, value in enumerate(row):
                tile.set_at((x, y), (value // 4,) * 3)
        pattern = pygame.Surface((max(width, 800) + 4, max(height, 480) + 4))
        for y in range(0, pattern.get_height(), 4):
            for x in range(0, pattern.get_width(), 4):
                pattern.blit(tile, (x, y))
        _dither_pattern = pattern
    offset = pygame.Rect(origin[0] % 4, origin[1] % 4, width, height)
    surface.blit(_dither_pattern, (0, 0), offset, special_flags=pygame.BLEND_ADD)


def circle(surface: pygame.Surface, center: tuple[float, float], radius: int, style: GlassStyle,
           color: tuple[int, int, int] | None = None) -> None:
    """A round glass button."""
    rect = pygame.Rect(0, 0, 2 * radius, 2 * radius)
    rect.center = (round(center[0]), round(center[1]))
    draw(surface, rect, radius, style, color)


def _blurred(area: pygame.Surface, factor: int) -> pygame.Surface:
    width, height = area.get_size()
    small = pygame.transform.smoothscale(area, (max(1, width // factor), max(1, height // factor)))
    return pygame.transform.smoothscale(small, (width, height)).convert_alpha()


def _layer(kind: str, size: tuple[int, int], radius: int, style: GlassStyle | None = None,
           color: tuple[int, int, int] | None = None) -> pygame.Surface:
    key = (kind, size, radius, style, color)
    if (cached := _cache.get(key)) is None:
        if len(_cache) > MAX_CACHED:
            _cache.clear()  # sizes change rarely; this only guards against unbounded growth
        if kind == "panel":
            cached = _panel(size, radius, style, color)
        elif kind == "solid":
            cached = _solid(size, radius, color)
        else:
            cached = _BUILDERS[kind](size, radius, style)
        _cache[key] = cached
    return cached


def _solid(size: tuple[int, int], radius: int, color: tuple[int, int, int]) -> pygame.Surface:
    """The shape filled with `color`, anti-aliased."""
    solid = _layer("mask", size, radius).copy()
    solid.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MULT)
    return solid


def _panel(size: tuple[int, int], radius: int, style: GlassStyle, color: tuple[int, int, int] | None) -> pygame.Surface:
    """Shadow, optional solid tint, body and rim composited into one image (padded for the shadow).

    The result has premultiplied alpha and must be blitted with BLEND_PREMULTIPLIED. pygame's plain blit of one
    translucent surface onto another treats the bottom one as opaque, so the cocoa shadow would bleed into the
    frosted body and turn every panel muddy grey-brown.
    """
    pad = style.shadow_blur * 2 if style.shadow else 0
    canvas = (size[0] + 2 * pad, size[1] + 2 * pad + style.shadow_offset)
    panel = pygame.Surface(canvas, pygame.SRCALPHA)
    panel.fill((0, 0, 0, 0))
    mask = _layer("mask", size, radius)
    layers: list[tuple[pygame.Surface, tuple[int, int]]] = []
    if style.shadow:
        # Only around the shape: light passes through glass, so its own shadow mustn't darken it.
        shadow = pygame.Surface(canvas, pygame.SRCALPHA)
        shadow.fill((*SHADOW_COLOR, 0))
        shadow.blit(_shadow(size, radius, style), (0, style.shadow_offset))
        cut = pygame.Surface(canvas, pygame.SRCALPHA)
        cut.fill((0, 0, 0, 0))
        cut.blit(mask, (pad, pad))
        cut.fill((0, 0, 0, 255), special_flags=pygame.BLEND_RGBA_MULT)  # keep only the mask's alpha
        shadow.blit(cut, (0, 0), special_flags=pygame.BLEND_RGBA_SUB)
        layers.append((shadow, (0, 0)))
    if color is not None:
        layers.append((_solid(size, radius, color), (pad, pad)))
    layers.append((_body(size, radius, style), (pad, pad)))
    layers.append((_rim(size, radius, style), (pad, pad)))
    for layer, position in layers:
        panel.blit(layer.premul_alpha(), position, special_flags=pygame.BLEND_PREMULTIPLIED)
    return panel


def _mask(size: tuple[int, int], radius: int, _style=None) -> pygame.Surface:
    width, height = size
    scale = _scale(size)
    big = pygame.Surface((width * scale, height * scale), pygame.SRCALPHA)
    big.fill(CLEAR)
    pygame.draw.rect(big, (255, 255, 255, 255), big.get_rect(), border_radius=radius * scale)
    return pygame.transform.smoothscale(big, size)


def _vertical(size: tuple[int, int], top: tuple[int, int, int, int], bottom: tuple[int, int, int, int],
              ease: bool = False) -> pygame.Surface:
    """A top→bottom gradient. With `ease`, it changes fastest at the top and flattens out smoothly towards the
    bottom ((1 - t)²): a highlight that fades without a visible edge. (It used to stop dead at 55 % of the
    height, which left a line across every panel.)"""
    width, height = size
    column = pygame.Surface((1, height), pygame.SRCALPHA)
    for y in range(height):
        t = y / max(1, height - 1)
        weight = (1 - t) ** 2 if ease else 1 - t
        column.set_at((0, y), tuple(round(b + (a - b) * weight) for a, b in zip(top, bottom)))
    return pygame.transform.scale(column, (width, height))


def _body(size: tuple[int, int], radius: int, style: GlassStyle) -> pygame.Surface:
    r, g, b, a = style.fill
    tint = _vertical(size, (r, g, b, min(255, a + style.highlight)), (r, g, b, a), ease=True)
    tint.blit(_layer("mask", size, radius), (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return tint


def _rim(size: tuple[int, int], radius: int, style: GlassStyle) -> pygame.Surface:
    """A 1 px light edge: bright along the top (where light catches it), fainter below."""
    width, height = size
    scale = _scale(size)
    big = pygame.Surface((width * scale, height * scale), pygame.SRCALPHA)
    big.fill(CLEAR)
    pygame.draw.rect(big, (255, 255, 255, 255), big.get_rect(), border_radius=radius * scale)
    inner = big.get_rect().inflate(-2 * scale, -2 * scale)
    pygame.draw.rect(big, CLEAR, inner, border_radius=max(0, (radius - 1) * scale))
    ring = pygame.transform.smoothscale(big, size)
    fade = _vertical(size, (255, 255, 255, style.rim_top), (255, 255, 255, style.rim_bottom))
    ring.blit(fade, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    return ring


def _shadow(size: tuple[int, int], radius: int, style: GlassStyle) -> pygame.Surface:
    """A soft drop shadow: the shape drawn small, padded, and scaled up (a cheap blur)."""
    pad = style.shadow_blur * 2
    width, height = size[0] + 2 * pad, size[1] + 2 * pad
    shrink = max(2, style.shadow_blur)
    small = pygame.Surface((max(1, width // shrink), max(1, height // shrink)), pygame.SRCALPHA)
    small.fill((*SHADOW_COLOR, 0))
    inset = pygame.Rect(pad // shrink, pad // shrink, max(1, size[0] // shrink), max(1, size[1] // shrink))
    pygame.draw.rect(small, (*SHADOW_COLOR, style.shadow), inset, border_radius=max(1, radius // shrink))
    return pygame.transform.smoothscale(small, (width, height))


_BUILDERS = {"mask": _mask, "body": _body, "rim": _rim, "shadow": _shadow}
