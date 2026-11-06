"""Screen geometry, colors and fonts."""

from __future__ import annotations

import struct
from collections import OrderedDict
from pathlib import Path

import pygame

WIDTH, HEIGHT = 800, 480
TAB_BAR_HEIGHT = 64
# The tab bar is a floating glass capsule inside that strip (deck/ui/glass.py).
BAR_MARGIN, BAR_TOP, BAR_HEIGHT = 8, 6, 52
TOUCH_SLOP = 14  # hit areas extend this far past what's drawn

# Warm and creamy: latte-cream base, cocoa-brown text, one terracotta accent.
BACKGROUND = (226, 211, 188)  # latte cream
SURFACE = (214, 197, 171)  # cards
SURFACE_HIGH = (201, 182, 154)  # pills and buttons on cards, the active tab
PRESSED = (185, 164, 134)
TEXT = (64, 44, 32)  # cocoa
TEXT_DIM = (106, 80, 62)
TEXT_FAINT = (138, 114, 92)
TRACK = (196, 178, 151)
ACCENT = (192, 104, 62)  # terracotta: play button, progress, sliders, master
ACCENT_PRESSED = (168, 88, 50)
KNOB = (245, 236, 221)  # slider/progress knobs: light cream with an accent ring
WARNING = (200, 136, 44)  # amber
MUTED = (180, 80, 74)  # warm red
CONNECTED = (100, 144, 88)  # sage

# The ambient view stays dark (it's for the evening), so it has its own light text color.
AMBIENT_TEXT = (246, 238, 225)

RADIUS_CARD = 22
RADIUS_ART = 20

FONT_DIR = Path(__file__).resolve().parent.parent.parent / "fonts"

# Per character, the first font in the chain that has it is used: Fredoka (bundled, Latin only), a system sans
# for symbols (DejaVu on the Pi, Segoe UI on Windows), a Korean font (Nanum Gothic on the Pi: `apt install
# fonts-nanum`; Malgun Gothic on Windows), then a CJK font for kanji/kana as the last resort. Droid Sans
# Fallback has no Hangul at all, so without Nanum Korean titles render as empty boxes on the Pi.
_FONT_FILES = {
    "regular": [
        [FONT_DIR / "Fredoka-Medium.ttf"],
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/segoeui.ttf"],
        ["/usr/share/fonts/truetype/nanum/NanumGothic.ttf", "C:/Windows/Fonts/malgun.ttf"],
        ["/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", "C:/Windows/Fonts/malgun.ttf"],
    ],
    "bold": [
        [FONT_DIR / "Fredoka-SemiBold.ttf"],
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/segoeuib.ttf"],
        ["/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf", "C:/Windows/Fonts/malgunbd.ttf"],
        ["/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf", "C:/Windows/Fonts/malgunbd.ttf"],
    ],
}
_MAX_CACHED_TEXT = 256


class Fonts:
    """Font loading with per-string fallback, and a cache of rendered text (slow to render on the Pi Zero)."""

    def __init__(self) -> None:
        self._chains = {
            # dict.fromkeys: drop duplicates (on Windows, Malgun Gothic is both the Korean font and the last resort)
            style: list(dict.fromkeys(
                path for candidates in chain if (path := next((p for p in map(Path, candidates) if p.exists()), None))
            ))
            for style, chain in _FONT_FILES.items()
        }
        self._fonts: dict[tuple[Path | None, int], pygame.font.Font] = {}
        self._coverage: dict[Path, frozenset[int]] = {}
        self._text: OrderedDict[tuple, pygame.Surface] = OrderedDict()

    def render(
        self, text: str, size: int, color: tuple[int, int, int], bold: bool = False, max_width: int | None = None
    ) -> pygame.Surface:
        key = (text, size, color, bold, max_width)
        if (cached := self._text.get(key)) is not None:
            self._text.move_to_end(key)
            return cached

        if max_width is not None and self._width(self.runs(text, bold), size) > max_width:
            text = self._ellipsize(text, size, bold, max_width)
        surface = self._draw_runs(self.runs(text, bold), size, color)

        self._text[key] = surface
        if len(self._text) > _MAX_CACHED_TEXT:
            self._text.popitem(last=False)
        return surface

    def runs(self, text: str, bold: bool = False) -> list[tuple[Path | None, str]]:
        """Splits `text` into (font, text) runs: each character uses the first font in the chain that has it.

        Mixed titles like "抱かれに来た女 — Kingo Hamada" need this: the CJK fallback has no Latin letters
        and Fredoka has no CJK. Whitespace stays with the run before it, so spaces don't split runs.
        """
        chain = self._chains["bold" if bold else "regular"] or [None]
        runs: list[tuple[Path | None, str]] = []
        for char in text:
            if char.isspace() and runs:
                path = runs[-1][0]
            else:
                path = next((p for p in chain[:-1] if self._covers(p, char, bold)), chain[-1])
            if runs and runs[-1][0] == path:
                runs[-1] = (path, runs[-1][1] + char)
            else:
                runs.append((path, char))
        return runs

    def _width(self, runs: list[tuple[Path | None, str]], size: int) -> int:
        return sum(self._font(path, size).size(part)[0] for path, part in runs)

    def _draw_runs(self, runs: list[tuple[Path | None, str]], size: int, color: tuple[int, int, int]) -> pygame.Surface:
        if len(runs) <= 1:
            path, part = runs[0] if runs else (None, "")
            return self._font(path, size).render(part, True, color)
        fonts = [(self._font(path, size), part) for path, part in runs]
        ascent = max(font.get_ascent() for font, _ in fonts)
        descent = max(-font.get_descent() for font, _ in fonts)
        rendered = [(font, font.render(part, True, color)) for font, part in fonts]
        surface = pygame.Surface((sum(r.get_width() for _, r in rendered), ascent + descent), pygame.SRCALPHA)
        x = 0
        for font, image in rendered:
            surface.blit(image, (x, ascent - font.get_ascent()))  # share one baseline
            x += image.get_width()
        return surface

    def _ellipsize(self, text: str, size: int, bold: bool, max_width: int) -> str:
        low, high = 0, len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if self._width(self.runs(text[:mid].rstrip() + "…", bold), size) <= max_width:
                low = mid
            else:
                high = mid - 1
        return text[:low].rstrip() + "…"

    def _covers(self, path: Path | None, text: str, bold: bool) -> bool:
        chain = self._chains["bold" if bold else "regular"]
        if path is None or not chain or path == chain[-1]:
            return True  # the last resort is never ruled out (and its large cmap is never parsed)
        if path not in self._coverage:
            self._coverage[path] = _codepoints(path)
        coverage = self._coverage[path]
        return all(ord(char) in coverage for char in text)

    def _font(self, path: Path | None, size: int) -> pygame.font.Font:
        key = (path, size)
        if key not in self._fonts:
            self._fonts[key] = pygame.font.Font(path, size)
        return self._fonts[key]


def _codepoints(path: Path) -> frozenset[int]:
    """Characters a TrueType/OpenType font maps to a glyph, from its cmap table (format 12 or 4).

    pygame can't tell: Font.metrics() reports the placeholder glyph's metrics for missing characters.
    """
    data = path.read_bytes()
    (num_tables,) = struct.unpack_from(">H", data, 4)
    for index in range(num_tables):
        tag, _, cmap, _ = struct.unpack_from(">4sIII", data, 12 + 16 * index)
        if tag == b"cmap":
            break
    else:
        return frozenset()

    (count,) = struct.unpack_from(">H", data, cmap + 2)
    subtables = {}
    for index in range(count):
        platform, encoding, offset = struct.unpack_from(">HHI", data, cmap + 4 + 8 * index)
        subtables[(platform, encoding)] = cmap + offset
    start = next((subtables[k] for k in ((3, 10), (0, 4), (3, 1), (0, 3)) if k in subtables), None)
    if start is None:
        return frozenset()

    points: set[int] = set()
    (fmt,) = struct.unpack_from(">H", data, start)
    if fmt == 12:
        (groups,) = struct.unpack_from(">I", data, start + 12)
        for group in range(groups):
            first, last, _ = struct.unpack_from(">III", data, start + 16 + 12 * group)
            points.update(range(first, last + 1))
    elif fmt == 4:
        (seg_x2,) = struct.unpack_from(">H", data, start + 6)
        segments = seg_x2 // 2
        ends = struct.unpack_from(f">{segments}H", data, start + 14)
        starts = struct.unpack_from(f">{segments}H", data, start + 16 + seg_x2)
        deltas = struct.unpack_from(f">{segments}h", data, start + 16 + 2 * seg_x2)
        range_offsets_at = start + 16 + 3 * seg_x2
        range_offsets = struct.unpack_from(f">{segments}H", data, range_offsets_at)
        for seg in range(segments):
            if starts[seg] == 0xFFFF:
                continue
            for char in range(starts[seg], ends[seg] + 1):
                if range_offsets[seg] == 0:
                    glyph = (char + deltas[seg]) & 0xFFFF
                else:
                    at = range_offsets_at + 2 * seg + range_offsets[seg] + 2 * (char - starts[seg])
                    (glyph,) = struct.unpack_from(">H", data, at)
                    glyph = (glyph + deltas[seg]) & 0xFFFF if glyph else 0
                if glyph:
                    points.add(char)
    return frozenset(points)

