"""Lights tab (one DIRIGERA room: power, brightness, warmth, per-lamp toggles) and the tab bar's quick toggle."""

from __future__ import annotations

import math
import time
from typing import Protocol

import pygame

from deck.state import Store
from deck.ui import glass, theme
from deck.ui.tab import Tab
from deck.ui.theme import Fonts
from deck.ui.widgets import Hit, bulb_icon, centered_text, circle_hit, filled_circle, knob, rounded

QUICK_KEY = "light:quick"
PRESETS = [("Warm", 2202), ("Soft", 2700), ("Neutral", 4000), ("Cool", 6500)]
LEVEL_PRESETS = [25, 50, 75, 100]
PILL_HEIGHT = 48
WARM_RGB, COOL_RGB = (255, 164, 84), (206, 226, 255)  # what 2200 K and 6500 K look like on the warmth slider
KELVIN_RANGE = (2202, 6535)

SEND_INTERVAL_S = 0.15  # Zigbee is slower than Core Audio; the hub client also coalesces what's queued
OVERRIDE_HOLD_S = 1.5
TRACK_HEIGHT = 18


class LightsApi(Protocol):
    def set(self, lamp_ids: list[str], **attributes) -> None: ...


def kelvin_rgb(kelvin: float) -> tuple[int, int, int]:
    t = min(max((kelvin - KELVIN_RANGE[0]) / (KELVIN_RANGE[1] - KELVIN_RANGE[0]), 0.0), 1.0)
    return tuple(round(w + (c - w) * t) for w, c in zip(WARM_RGB, COOL_RGB))  # type: ignore[return-value]


def reachable(store: Store) -> list[dict]:
    return [lamp for lamp in store.lamps or [] if lamp["reachable"]]


def toggle_all(store: Store, lights: LightsApi) -> None:
    """Everything off if any lamp is on, otherwise everything on."""
    lamps = reachable(store)
    if lamps:
        lights.set([lamp["id"] for lamp in lamps], isOn=not any(lamp["on"] for lamp in lamps))


def draw_quick_toggle(surface: pygame.Surface, store: Store, center: tuple[int, int], pressed: str | None) -> list[Hit]:
    """Round bulb button for the tab bar: filled when the room's lights are on. Hidden while the hub is unavailable."""
    lamps = reachable(store)
    if not lamps:
        return []
    on = any(lamp["on"] for lamp in lamps)
    if on:
        filled_circle(surface, center, 18, theme.ACCENT_PRESSED if pressed == QUICK_KEY else theme.ACCENT)
    else:
        glass.circle(surface, center, 18, glass.LENS_PRESSED if pressed == QUICK_KEY else glass.CHIP)
    bulb_icon(surface, center, 22, theme.BACKGROUND if on else theme.TEXT_DIM)
    return [circle_hit(QUICK_KEY, center, 18)]


class LightsTab(Tab):
    title = "Lights"
    needs_pc = False  # talks to the IKEA hub directly

    def __init__(self, store: Store, lights: LightsApi, fonts: Fonts, room_title: str) -> None:
        self._store = store
        self._lights = lights
        self._fonts = fonts
        self._room_title = room_title
        self._level_track = pygame.Rect(0, 0, 1, 1)
        self._temperature_track = pygame.Rect(0, 0, 1, 1)
        self._gradient: pygame.Surface | None = None
        self._backplate = glass.Backplate()
        # Dragged values shown instead of the hub's until its events catch up: name -> (value, show until).
        self._overrides: dict[str, tuple[float, float]] = {}
        self._pending: dict[str, int] = {}
        self._last_send = 0.0

    # --- state ---------------------------------------------------------------------------------------------

    def _group(self) -> dict:
        lamps = reachable(self._store)
        on = [lamp for lamp in lamps if lamp["on"]]
        source = on or lamps
        temps = [lamp for lamp in source if lamp["can_temperature"] and lamp["temperature"]]
        tunable = [lamp for lamp in lamps if lamp["can_temperature"] and lamp["warmest"] and lamp["coolest"]]
        now = time.monotonic()
        group = {
            "ids": [lamp["id"] for lamp in lamps],
            "on": bool(on),
            "level": max((lamp["level"] for lamp in source), default=0),
            "temperature": temps[0]["temperature"] if temps else None,
            "warmest": max((lamp["warmest"] for lamp in tunable), default=None),
            "coolest": min((lamp["coolest"] for lamp in tunable), default=None),
        }
        for name, (value, until) in self._overrides.items():
            if now < until:
                group[name] = value
        return group

    # --- input ---------------------------------------------------------------------------------------------

    def on_tap(self, key: str, pos: tuple[float, float]) -> None:
        group = self._group()
        if key == "light:power":
            toggle_all(self._store, self._lights)
        elif key in ("light:level", "light:temperature"):
            self._set_slider(key, pos[0], final=True)
        elif key.startswith("light:preset:"):
            kelvin = self._clamp_kelvin(int(key.removeprefix("light:preset:")), group)
            self._override("temperature", kelvin, final=True)
            self._lights.set(group["ids"], isOn=True, colorTemperature=kelvin)
        elif key.startswith("light:level:"):
            level = int(key.removeprefix("light:level:"))
            self._override("level", level, final=True)
            self._lights.set(group["ids"], isOn=True, lightLevel=level)

    def on_drag(self, key: str, pos: tuple[float, float]) -> None:
        if key in ("light:level", "light:temperature"):
            self._set_slider(key, pos[0], final=False)

    def on_drag_end(self, key: str, pos: tuple[float, float]) -> None:
        if key in ("light:level", "light:temperature"):
            self._set_slider(key, pos[0], final=True)

    def busy(self) -> bool:
        return bool(self._pending)

    def tick(self) -> bool:
        now = time.monotonic()
        if self._pending and now - self._last_send >= SEND_INTERVAL_S:
            self._flush(now)
        expired = [name for name, (_, until) in self._overrides.items() if now >= until]
        for name in expired:
            del self._overrides[name]
        return bool(expired)

    def _set_slider(self, key: str, x: float, final: bool) -> None:
        group = self._group()
        if key == "light:level":
            fraction = (x - self._level_track.left) / self._level_track.width
            name, attribute, value = "level", "lightLevel", max(1, round(min(max(fraction, 0.0), 1.0) * 100))
        else:
            if group["warmest"] is None:
                return
            fraction = min(max((x - self._temperature_track.left) / self._temperature_track.width, 0.0), 1.0)
            kelvin = group["warmest"] + fraction * (group["coolest"] - group["warmest"])
            name, attribute, value = "temperature", "colorTemperature", round(kelvin / 10) * 10
        self._override(name, value, final)
        self._pending[attribute] = value
        now = time.monotonic()
        if final or now - self._last_send >= SEND_INTERVAL_S:
            self._flush(now)

    def _flush(self, now: float) -> None:
        ids = self._group()["ids"]
        if ids and self._pending:
            self._lights.set(ids, isOn=True, **self._pending)
        self._pending = {}
        self._last_send = now

    def _override(self, name: str, value: float, final: bool) -> None:
        self._overrides[name] = (value, time.monotonic() + OVERRIDE_HOLD_S if final else math.inf)

    @staticmethod
    def _clamp_kelvin(kelvin: int, group: dict) -> int:
        if group["warmest"] is None:
            return kelvin
        return int(min(max(kelvin, group["warmest"]), group["coolest"]))

    # --- drawing -------------------------------------------------------------------------------------------

    def draw(self, surface: pygame.Surface, area: pygame.Rect, pressed: str | None) -> list[Hit]:
        f = self._fonts
        if self._store.lamps is None:
            centered_text(surface, f.render("Lights unavailable", 28, theme.TEXT_DIM, bold=True), (area.centerx, area.centery - 16))
            centered_text(surface, f.render("Looking for the IKEA hub…", 18, theme.TEXT_FAINT), (area.centerx, area.centery + 22))
            return []
        if not self._store.lamps:
            centered_text(surface, f.render(f"No lights in {self._room_title}", 24, theme.TEXT_DIM, bold=True), area.center)
            return []

        group = self._group()
        left = pygame.Rect(area.left + 16, area.top + 8, 300, area.height - 16)
        right = pygame.Rect(left.right + 12, left.top, area.right - 16 - left.right - 12, left.height)
        self._backplate.draw(surface, area, theme.BACKGROUND, self._panels(left, right, group, pressed))
        hits = self._draw_power(surface, left, group, pressed)
        hits += self._draw_controls(surface, right, group, pressed)
        return hits

    def _draw_power(self, surface: pygame.Surface, card: pygame.Rect, group: dict, pressed: str | None) -> list[Hit]:
        f = self._fonts
        lamps = reachable(self._store)
        on_count = sum(lamp["on"] for lamp in lamps)
        surface.blit(f.render(self._room_title, 22, theme.TEXT, bold=True, max_width=card.width - 48), (card.left + 24, card.top + 22))
        status = f"On · {group['level']}%" if on_count else "Off"  # the lamps are controlled as one
        surface.blit(f.render(status, 16, theme.TEXT_DIM), (card.left + 24, card.top + 56))

        center = self._power_center(card)
        bulb_icon(surface, center, 72, theme.BACKGROUND if group["on"] else theme.TEXT_FAINT)
        hint = f.render("Tap to turn off" if group["on"] else "Tap to turn on", 15, theme.TEXT_FAINT)
        surface.blit(hint, hint.get_rect(midbottom=(card.centerx, card.bottom - 20)))
        return [circle_hit("light:power", center, 80)]

    def _draw_controls(self, surface: pygame.Surface, card: pygame.Rect, group: dict, pressed: str | None) -> list[Hit]:
        f = self._fonts
        x0, width = card.left + 24, card.width - 48
        hits: list[Hit] = []

        # Brightness: slider + 25/50/75/100 % presets
        self._label(surface, "Brightness", f"{group['level']}%", x0, width, card.top + 32)
        self._level_track = pygame.Rect(x0, card.top + 72, width, TRACK_HEIGHT)
        pygame.draw.rect(surface, theme.TRACK, self._level_track, border_radius=TRACK_HEIGHT // 2)
        fill = self._level_track.copy()
        fill.width = max(TRACK_HEIGHT, round(width * group["level"] / 100))
        pygame.draw.rect(surface, theme.ACCENT if group["on"] else theme.TEXT_FAINT, fill, border_radius=TRACK_HEIGHT // 2)
        knob(surface, (x0 + round(width * group["level"] / 100), self._level_track.centery),
             16 if pressed == "light:level" else 14, theme.ACCENT if group["on"] else theme.TEXT_FAINT)
        hits.append(Hit("light:level", self._level_track.inflate(24, 32), draggable=True, horizontal=True))
        rows = self._preset_rows(card, group)
        hits += self._preset_row(surface, x0, width, *rows[0])

        # Warmth (only if the lamps support colour temperature): slider + Warm/Soft/Neutral/Cool presets
        if group["warmest"] is not None and group["temperature"] is not None:
            kelvin = group["temperature"]
            self._label(surface, "Warmth", f"{round(kelvin / 10) * 10} K", x0, width, card.top + 196)
            self._temperature_track = pygame.Rect(x0, card.top + 236, width, TRACK_HEIGHT)
            surface.blit(self._gradient_surface(group), self._temperature_track)
            span = group["coolest"] - group["warmest"] or 1
            position = x0 + round(width * min(max((kelvin - group["warmest"]) / span, 0.0), 1.0))
            knob(surface, (position, self._temperature_track.centery), 16 if pressed == "light:temperature" else 14, kelvin_rgb(kelvin))
            hits.append(Hit("light:temperature", self._temperature_track.inflate(24, 32), draggable=True, horizontal=True))
            hits += self._preset_row(surface, x0, width, *rows[1])
        return hits

    @staticmethod
    def _power_center(card: pygame.Rect) -> tuple[int, int]:
        return (card.centerx, card.top + 228)

    def _preset_rows(self, card: pygame.Rect, group: dict) -> list[tuple[int, list[tuple[str, str, tuple[int, int, int] | None, bool]]]]:
        """(top, presets) per row of preset pills: brightness, then warmth if the lamps support it."""
        rows = [(card.top + 110,
                 [(f"light:level:{level}", f"{level}%", None, group["on"] and abs(group["level"] - level) <= 2)
                  for level in LEVEL_PRESETS])]
        if group["warmest"] is not None and group["temperature"] is not None:
            kelvin = group["temperature"]
            presets = [(name, self._clamp_kelvin(preset, group)) for name, preset in PRESETS]
            rows.append((card.top + 274,
                         [(f"light:preset:{preset}", name, kelvin_rgb(preset), group["on"] and abs(kelvin - preset) < 150)
                          for name, preset in presets]))
        return rows

    @staticmethod
    def _pill_rects(x0: int, width: int, top: int, count: int) -> list[pygame.Rect]:
        gap = 8
        pill_width = (width - gap * (count - 1)) // count
        return [pygame.Rect(x0 + index * (pill_width + gap), top, pill_width, PILL_HEIGHT) for index in range(count)]

    def _panels(self, left: pygame.Rect, right: pygame.Rect, group: dict, pressed: str | None) -> list[glass.Panel]:
        """All of the tab's glass: both cards, the power button with its halo, the preset pills."""
        panels = [glass.panel(left, theme.RADIUS_CARD, glass.CARD), glass.panel(right, theme.RADIUS_CARD, glass.CARD)]
        # A glass halo around the button; the button is terracotta tinted glass when on, a clear lens when off.
        center = self._power_center(left)
        panels.append(glass.circle_panel(center, 92, glass.CHIP))
        if group["on"]:
            style = glass.TINTED_PRESSED if pressed == "light:power" else glass.TINTED
            panels.append(glass.circle_panel(center, 76, style, theme.ACCENT))
        else:
            panels.append(glass.circle_panel(center, 76, glass.LENS_PRESSED if pressed == "light:power" else glass.LENS))
        x0, width = right.left + 24, right.width - 48
        for top, presets in self._preset_rows(right, group):
            for pill, (key, _, _, selected) in zip(self._pill_rects(x0, width, top, len(presets)), presets):
                if selected:
                    style = glass.TINTED_PRESSED if pressed == key else glass.TINTED
                    panels.append(glass.panel(pill, PILL_HEIGHT // 2, style, theme.ACCENT))
                else:
                    panels.append(glass.panel(pill, PILL_HEIGHT // 2, glass.LENS_PRESSED if pressed == key else glass.LENS))
        return panels

    def _preset_row(
        self, surface: pygame.Surface, x0: int, width: int, top: int,
        presets: list[tuple[str, str, tuple[int, int, int] | None, bool]],
    ) -> list[Hit]:
        """A row of pill labels (key, label, optional color dot, selected); their glass is in the backplate."""
        f = self._fonts
        hits = []
        for pill, (key, text, dot, selected) in zip(self._pill_rects(x0, width, top, len(presets)), presets):
            label = f.render(text, 16, theme.BACKGROUND if selected else theme.TEXT, bold=selected)
            if dot is None:
                surface.blit(label, label.get_rect(center=pill.center))
            else:
                filled_circle(surface, (pill.left + 18, pill.centery), 6, dot)
                surface.blit(label, label.get_rect(midleft=(pill.left + 30, pill.centery)))
            hits.append(Hit(key, pill.inflate(0, 8)))
        return hits

    def _label(self, surface: pygame.Surface, text: str, value: str, x0: int, width: int, y: int) -> None:
        f = self._fonts
        surface.blit(f.render(text, 18, theme.TEXT, bold=True), (x0, y))
        rendered = f.render(value, 16, theme.TEXT_DIM)
        surface.blit(rendered, (x0 + width - rendered.get_width(), y + 2))

    def _gradient_surface(self, group: dict) -> pygame.Surface:
        """Warm-to-cool track, rendered once (rounded, with alpha corners: the glass card behind it is translucent)."""
        size = self._temperature_track.size
        if self._gradient is None or self._gradient.get_size() != size:
            strip = pygame.Surface((64, 1))
            for x in range(64):
                kelvin = group["warmest"] + (group["coolest"] - group["warmest"]) * x / 63
                strip.set_at((x, 0), kelvin_rgb(kelvin))
            gradient = pygame.transform.smoothscale(strip, size)
            self._gradient = rounded(gradient, TRACK_HEIGHT // 2)
        return self._gradient
