"""pygame main loop: tab bar, input dispatch and redraw-on-change."""

from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass
from pathlib import Path

import pygame

from deck.backlight import Backlight
from deck.config import Config
from deck.idle import IdleTimer
from deck.net import NetClient
from deck.state import Store
from deck.ui import glass, theme
from deck.lights import LightsClient
from deck.ui.ambient import AmbientView
from deck.ui.lights import QUICK_KEY, LightsApi, LightsTab, draw_quick_toggle, reachable, toggle_all
from deck.ui.outputs import OutputSelector
from deck.ui.polarity import PolarityTab
from deck.ui.power import PowerControl
from deck.ui.spotify import SpotifyTab
from deck.ui.tab import Tab
from deck.ui.theme import Fonts
from deck.ui.volume import VolumeTab
from deck.ui.widgets import Hit, centered_text, power_icon
from deck.wol import WakeOnLan

log = logging.getLogger(__name__)

IDLE_WAIT_MS = 1000
ANIMATING_WAIT_MS = 250  # fallback redraw interval for a tab that animates but doesn't say when it changes
GESTURE_WAIT_MS = 50  # while a finger is down or a throttled command is pending
DRAG_THRESHOLD_PX = 10  # movement before a press becomes a drag or scroll instead of a tap
FADE_TO_AMBIENT_S = 1.0  # slow and calm into the album-cover view
FADE_FROM_AMBIENT_S = 0.35  # quick back out, so a touch feels responsive
FADE_WAIT_MS = 16
LENS_SLIDE_S = 0.32  # the selected-tab lens glides to the new tab; the page itself switches instantly
LENS_FPS = 60  # during the slide only the bar strip is redrawn and sent: labwc manages ~45 fps for that vs ~28 full-frame
WAKE_KEY = "wake:pc"
WAKE_BOOT_S = 120  # after "Turn on PC", show "Starting…" this long (the PC boots and the companion autostarts)


@dataclass
class _Fade:
    """A crossfade between two full-screen snapshots."""

    started: float
    duration: float
    source: pygame.Surface
    target: pygame.Surface

    def progress(self, now: float) -> float:
        t = min(max((now - self.started) / self.duration, 0.0), 1.0)
        return t * t * (3 - 2 * t)  # smoothstep: eases in and out


@dataclass
class _Gesture:
    hit: Hit
    start: tuple[float, float]
    last: tuple[float, float]
    axis: str | None = None  # "x" = scroll, "y" = drag; None until the threshold is passed


class App:
    def __init__(
        self,
        screen: pygame.Surface,
        store: Store,
        net: NetClient,
        fonts: Fonts,
        idle: IdleTimer | None = None,
        ambient_brightness: float = 0.6,
        lights: LightsApi | None = None,
        lights_title: str = "Lights",
        backlight: Backlight | None = None,
        dark_after_s: float = 0,
        wol: WakeOnLan | None = None,
        idle_quiet_s: float = 0,
    ) -> None:
        self._idle_quiet_s = idle_quiet_s
        self._wol = wol
        self._waking_until: float | None = None  # "Turn on PC" was tapped; the PC should be booting
        self._screen = screen
        self._store = store
        self._fonts = fonts
        self._idle = idle
        self._backlight = backlight
        self._dark_after_s = dark_after_s
        self._offline_since: float | None = None if store.connected else time.monotonic()
        self._dark_shown = False  # whether the screen currently shows (or is fading to) dark
        self._lights = lights
        self._ambient = AmbientView(store, fonts, ambient_brightness)
        self._outputs = OutputSelector(store, net, fonts)
        self._power = PowerControl(store, net, fonts)
        # The Polarity tab is always in the list but only shown (and reachable) while Polarity runs on the PC.
        self._tabs: list[Tab] = [VolumeTab(store, net, fonts), SpotifyTab(store, net, fonts), PolarityTab(store, net, fonts)]
        if lights is not None:
            self._tabs.append(LightsTab(store, lights, fonts, lights_title))
        self._active = 1
        self._hits: list[Hit] = []
        self._gesture: _Gesture | None = None
        self._waking_touch = False  # the touch that left the ambient view; ignored until released
        self._fade: _Fade | None = None
        self._bar_backplate = glass.Backplate()
        # Lens slide: where it started (x) and when; None when it's resting on the active tab.
        self._lens_from: float | None = None
        self._lens_started = 0.0
        self._lens_x: float | None = None  # where it was last drawn (a new slide starts from there)
        # While a tab animates (a playing progress bar): when its next redraw is due. Only then, not every loop:
        # redrawing at the 30 fps cap kept the deck at 35 % CPU and labwc at 83 % while music played.
        self._next_animation_at = 0.0
        self._warmed: set[int] = set()  # tabs drawn once off-screen, so their glass is cached before the first visit
        self.pressed: str | None = None
        self.dirty = True

    @property
    def ambient(self) -> bool:
        return self._idle is not None and self._idle.idle

    @property
    def dark(self) -> bool:
        """Idle with the PC gone for a while (it's off): black screen, backlight off. A touch or the PC wakes it."""
        return (self.ambient and self._dark_after_s > 0 and self._offline_since is not None
                and time.monotonic() - self._offline_since >= self._dark_after_s)

    def needs_draw(self) -> bool:
        return (self.dirty or self._fade is not None
                or (self.animating() and time.monotonic() >= self._next_animation_at))

    def select_tab(self, title: str) -> None:
        self._active = next(i for i, tab in enumerate(self._tabs) if tab.title.lower() == title.lower())
        self.dirty = True

    @property
    def _tab(self) -> Tab:
        return self._tabs[self._active]

    def animating(self) -> bool:
        return not self.ambient and self._store.connected and self._tab.animating()

    def wait_ms(self) -> int:
        if self._lens_from is not None:
            return 1  # presenting the bar strip paces the slide (~45 fps); a 16 ms wait on top halved it
        if self._fade is not None:
            return FADE_WAIT_MS
        if self._gesture is not None or self._tab.busy():
            return GESTURE_WAIT_MS
        wait = IDLE_WAIT_MS
        if self.animating():
            wait = max(1, min(wait, round((self._next_animation_at - time.monotonic()) * 1000)))
        if self._frames_active() and (interval := self._tab.frame_interval_ms()) is not None:
            wait = min(wait, interval)
        return wait

    def _frames_active(self) -> bool:
        return (not self.ambient and self._fade is None and self._store.connected
                and not self._outputs.open and not self._power.open)

    @property
    def lens_moving(self) -> bool:
        return self._lens_from is not None

    def draw_frame(self) -> list[pygame.Rect]:
        """Partial redraw of self-moving parts (the sliding tab lens, a scrolling title); returns the rects to update."""
        rects = []
        if self._lens_from is not None and not self.ambient and self._fade is None:
            # Just the bar strip: the page below was drawn in full when the tab was picked.
            strip = pygame.Rect(0, 0, theme.WIDTH, theme.TAB_BAR_HEIGHT)
            self._screen.fill(theme.BACKGROUND, strip)
            self._draw_tab_bar()
            rects.append(strip)
        if self._frames_active() and self._tab.frame_interval_ms() is not None:
            rects += self._tab.draw_frame(self._screen)
        return rects

    def tick(self) -> None:
        if not self._tab.visible():
            # Polarity closed, or the tab was turned off in the PC app's settings: Spotify, else Volume.
            spotify = next(i for i, tab in enumerate(self._tabs) if isinstance(tab, SpotifyTab))
            self._active = spotify if self._tabs[spotify].visible() else 0
            self.dirty = True
        if (self._outputs.open and not self._store.shows("outputs")) or (self._power.open and not self._store.shows("power")):
            self._outputs.open = self._power.open = False
            self.dirty = True
        if self._tab.tick():
            self.dirty = True
        if self._gesture is None and self._fade is None and not self.ambient:
            self._warm_next_tab()
        if self._waking_until is not None and (self._store.connected or time.monotonic() >= self._waking_until):
            self._waking_until = None
            self.dirty = True
        if self._store.connected:
            self._offline_since = None
        elif self._offline_since is None:
            self._offline_since = time.monotonic()
        if self._idle is not None and self._gesture is None and self._idle.update(self._idle_wait()):
            self._outputs.open = self._power.open = False
            self._start_fade(FADE_TO_AMBIENT_S)
        if self.dark != self._dark_shown:
            # The PC went away while idle (fade to black), or came back (fade up to the album cover).
            log.info("PC %s: screen %s", "gone" if self.dark else "back", "dark" if self.dark else "on")
            self._start_fade(FADE_TO_AMBIENT_S)
        if self.ambient and not self.dark and self._fade is None and self._ambient.minute_changed():
            self.dirty = True  # the idle clock
        if (self._outputs.open or self._power.open) and not self._store.connected:
            self._outputs.open = self._power.open = False
            self.dirty = True

    def _warm_next_tab(self) -> None:
        """Draws one not-yet-visited tab off-screen once its data is there (building its glass takes up to
        ~0.6 s on the Pi Zero; better now than on the first tap)."""
        self._warmed.add(self._active)
        for index, tab in enumerate(self._tabs):
            if index in self._warmed or not tab.visible() or (tab.needs_pc and not self._store.connected):
                continue
            if isinstance(tab, LightsTab) and not self._store.lamps:
                continue
            scratch = pygame.Surface(self._screen.get_size()).convert()
            scratch.fill(theme.BACKGROUND)
            content = pygame.Rect(0, theme.TAB_BAR_HEIGHT, theme.WIDTH, theme.HEIGHT - theme.TAB_BAR_HEIGHT)
            tab.draw(scratch, content, None)
            self._warmed.add(index)
            return  # one per tick, so the loop never stalls for long

    def _idle_wait(self) -> float | None:
        """A short idle timeout while there's nothing to control: the PC is gone and no light is on.

        None = the configured timeout. Not while "Starting the PC…" is showing, so that stays readable.
        """
        if self._idle_quiet_s <= 0 or self._store.connected or self._waking_until is not None:
            return None
        if any(lamp["on"] for lamp in reachable(self._store)):
            return None
        return self._idle_quiet_s

    def pointer_down(self, pos: tuple[float, float]) -> None:
        if self._idle is not None and self._idle.activity():
            self._waking_touch = True
            self._start_fade(FADE_FROM_AMBIENT_S)
            return
        hit = self._hit_at(pos)
        self._gesture = _Gesture(hit, pos, pos) if hit else None
        self.pressed = hit.key if hit else None
        self.dirty = True

    def pointer_move(self, pos: tuple[float, float]) -> None:
        if self._waking_touch:
            return
        if self._idle is not None:
            self._idle.activity()
        gesture = self._gesture
        if gesture is None or not gesture.hit.draggable:
            return
        if gesture.axis is None:
            dx, dy = pos[0] - gesture.start[0], pos[1] - gesture.start[1]
            if max(abs(dx), abs(dy)) < DRAG_THRESHOLD_PX:
                return
            # "y" = drag the control under the finger; horizontal sliders drag in any direction.
            gesture.axis = "y" if gesture.hit.horizontal or abs(dy) >= abs(dx) else "x"
            if gesture.axis == "x":
                self.pressed = None
                self._tab.on_scroll_begin(gesture.hit.key)
        if gesture.axis == "x":
            self._tab.on_scroll(pos[0] - gesture.last[0])
        else:
            self._tab.on_drag(gesture.hit.key, pos)
        gesture.last = pos
        self.dirty = True

    def pointer_up(self, pos: tuple[float, float]) -> None:
        if self._waking_touch:
            self._waking_touch = False
            return
        gesture, self._gesture = self._gesture, None
        self.pressed = None
        self.dirty = True
        if gesture is None:
            return
        if gesture.axis == "x":
            self._tab.on_scroll_end()
        elif gesture.axis == "y":
            self._tab.on_drag_end(gesture.hit.key, pos)
        else:
            hit = self._hit_at(pos)
            if hit is not None and hit.key == gesture.hit.key:
                self._activate(hit.key, pos)

    def _activate(self, key: str, pos: tuple[float, float]) -> None:
        if self._power.handles(key):
            self._power.on_tap(key)
        elif self._outputs.handles(key):
            self._outputs.on_tap(key)
        elif key == WAKE_KEY and self._wol is not None:
            if self._wol.send():
                self._waking_until = time.monotonic() + WAKE_BOOT_S
        elif key == QUICK_KEY and self._lights is not None:
            toggle_all(self._store, self._lights)
        elif key.startswith("tab:"):
            index = int(key.removeprefix("tab:"))
            if index != self._active and self._lens_x is not None:
                self._lens_from, self._lens_started = self._lens_x, time.monotonic()
            self._active = index
        else:
            self._tab.on_tap(key, pos)

    def _hit_at(self, pos: tuple[float, float]) -> Hit | None:
        return next((hit for hit in reversed(self._hits) if hit.rect.collidepoint(pos)), None)

    def draw(self) -> None:
        fade = self._fade
        if fade is None:
            self._render()
            self._set_backlight()
            self._next_animation_at = time.monotonic() + self._tab.animation_delay_s()
            return
        progress = fade.progress(time.monotonic())
        if progress >= 1.0:
            self._fade = None
            self._render()  # the real frame, with anything that changed during the fade
            self._set_backlight()
            return
        self._screen.blit(fade.source, (0, 0))
        fade.target.set_alpha(round(255 * progress))
        self._screen.blit(fade.target, (0, 0))

    def _start_fade(self, duration: float) -> None:
        """Crossfade from what's on screen now to the next state (ambient flag already switched)."""
        source = self._screen.copy()
        self._render()
        self._fade = _Fade(time.monotonic(), duration, source, self._screen.copy())
        self._screen.blit(source, (0, 0))
        self.dirty = True
        self._dark_shown = self.dark
        if not self._dark_shown:
            self._set_backlight()  # light up before fading in

    def _set_backlight(self) -> None:
        """Off only once the screen has faded all the way to black."""
        if self._backlight is not None:
            self._backlight.set(not (self._dark_shown and self._fade is None))

    def _render(self) -> None:
        screen = self._screen
        if self.dark:
            screen.fill((0, 0, 0))
            self._hits = []
            self.dirty = False
            return
        if self.ambient:
            self._ambient.draw(screen)
            self._hits = []
            self.dirty = False
            return
        screen.fill(theme.BACKGROUND)
        hits = self._draw_tab_bar()
        content = pygame.Rect(0, theme.TAB_BAR_HEIGHT, theme.WIDTH, theme.HEIGHT - theme.TAB_BAR_HEIGHT)
        if self._store.connected or not self._tab.needs_pc:
            hits += self._tab.draw(screen, content, self.pressed)
        else:
            hits += self._draw_disconnected(content)
        # While a popup is open it takes all input; tapping outside closes it.
        self._hits = self._outputs.draw_menu(screen, self.pressed) or self._power.draw_dialog(screen, self.pressed) or hits
        self.dirty = False

    def _lens_position(self, target: float) -> float:
        """The lens's x now: easing (cubic ease-out) from where it was towards `target`, the active tab."""
        x = target
        if self._lens_from is not None:
            t = (time.monotonic() - self._lens_started) / LENS_SLIDE_S
            if t >= 1.0:
                self._lens_from = None
            else:
                x = self._lens_from + (target - self._lens_from) * (1 - (1 - max(t, 0.0)) ** 3)
        self._lens_x = x
        return x

    def _draw_tab_bar(self) -> list[Hit]:
        screen, f = self._screen, self._fonts
        # One floating glass capsule; the selected tab is a brighter glass lens inside it.
        bar = pygame.Rect(theme.BAR_MARGIN, theme.BAR_TOP, theme.WIDTH - 2 * theme.BAR_MARGIN, theme.BAR_HEIGHT)
        center_y = bar.centery
        shown = [index for index, tab in enumerate(self._tabs) if tab.visible() or index == self._active]
        tab_width = {1: 164, 2: 164, 3: 136}.get(len(shown), 112)  # 4 tabs (Polarity running): narrower
        pill_height, left = bar.height - 10, bar.left + 5
        pills: dict[int, pygame.Rect] = {}
        for slot, index in enumerate(shown):
            pill = pygame.Rect(left + slot * (tab_width + 4), 0, tab_width, pill_height)
            pill.centery = center_y
            pills[index] = pill

        # The selected tab's lens, gliding to a newly picked tab (ease-out). At rest it's part of the cached,
        # dithered backplate; while it moves it's one cached blit per frame on top.
        lens = pills[self._active].copy()
        lens.x = round(self._lens_position(lens.x))
        moving = self._lens_from is not None
        lens_style = glass.LENS_PRESSED if self.pressed == f"tab:{self._active}" else glass.LENS

        # The capsule and the resting lenses as one cached image (the strip behind them is always the plain page).
        panels = [glass.panel(bar, bar.height // 2, glass.BAR)]
        if not moving:
            panels.append(glass.panel(lens, pill_height // 2, lens_style))
        self._bar_backplate.draw(screen, pygame.Rect(0, 0, theme.WIDTH, theme.TAB_BAR_HEIGHT), theme.BACKGROUND, panels)
        if moving:
            glass.draw(screen, lens, pill_height // 2, lens_style)
        for index, pill in pills.items():  # a pressed tab that isn't selected: drawn directly, not cached
            if self.pressed == f"tab:{index}" and index != self._active:
                glass.draw(screen, pill, pill_height // 2, glass.LENS_PRESSED)

        hits = []
        for index, pill in pills.items():
            tab = self._tabs[index]
            key = f"tab:{index}"
            active = index == self._active
            label = f.render(tab.title, 20 if tab_width > 112 else 19, theme.TEXT if active else theme.TEXT_DIM, bold=active)
            centered_text(screen, label, pill.center)
            hits.append(Hit(key, pygame.Rect(pill.left, 0, pill.width, theme.TAB_BAR_HEIGHT)))

        # Right to left: output pill, quick light toggle (if lights are set up and reachable), PC power button.
        hits += self._outputs.draw_pill(screen, bar.right - 7, center_y, self.pressed, interactive=self._store.shows("outputs"))
        next_x = self._outputs.pill_left - 28
        if (self._lights is not None and self._store.shows("lights")
                and (light_hits := draw_quick_toggle(screen, self._store, (next_x, center_y), self.pressed))):
            hits += light_hits
            next_x -= 44
        if self._store.shows("power"):
            hits += self._power.draw_button(screen, (next_x, center_y), self.pressed)
        return hits

    def _draw_disconnected(self, area: pygame.Rect) -> list[Hit]:
        screen, f = self._screen, self._fonts
        if self._wol is None:
            centered_text(screen, f.render("Connecting to PC…", 28, theme.TEXT_DIM, bold=True), (area.centerx, area.centery - 16))
            centered_text(screen, f.render("Is pc-companion running?", 18, theme.TEXT_FAINT), (area.centerx, area.centery + 22))
            return []

        waking = self._waking_until is not None
        title, subtitle = (("Starting the PC…", "This takes about a minute") if waking
                           else ("The PC is off", "Or pc-companion isn't running"))
        centered_text(screen, f.render(title, 28, theme.TEXT_DIM, bold=True), (area.centerx, area.centery - 62))
        centered_text(screen, f.render(subtitle, 18, theme.TEXT_FAINT), (area.centerx, area.centery - 24))

        # Tapping again while it's starting sends another wake-up (harmless if the first one got through).
        button = pygame.Rect(0, 0, 230, 60)
        button.center = (area.centerx, area.centery + 52)
        pressed = self.pressed == WAKE_KEY
        if waking:
            ink = theme.TEXT_DIM
            glass.draw(screen, button, button.height // 2, glass.LENS_PRESSED if pressed else glass.LENS)
        else:
            ink = theme.BACKGROUND
            glass.draw(screen, button, button.height // 2, glass.TINTED_PRESSED if pressed else glass.TINTED, theme.ACCENT)
        label = f.render("Send again" if waking else "Turn on PC", 20, ink, bold=True)
        icon_x = button.centerx - (label.get_width() + 34) // 2 + 11
        power_icon(screen, (icon_x, button.centery), 24, ink)
        screen.blit(label, (icon_x + 23, button.centery - label.get_height() // 2))
        return [Hit(WAKE_KEY, button.inflate(theme.TOUCH_SLOP, theme.TOUCH_SLOP))]


def run(
    config: Config,
    screenshot: Path | None = None,
    screenshot_after_s: float = 5.0,
    start_tab: str | None = None,
    start_ambient: bool = False,
) -> None:
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((theme.WIDTH, theme.HEIGHT), pygame.FULLSCREEN if config.fullscreen else 0)
    pygame.display.set_caption("Pi Media Deck")
    pygame.mouse.set_visible(not config.fullscreen)
    log.info("display %s via %s", screen.get_size(), pygame.display.get_driver())

    wake_event = pygame.event.custom_type()

    def wake() -> None:
        try:
            pygame.event.post(pygame.event.Event(wake_event))
        except pygame.error:
            pass  # display already shut down

    inbox: queue.Queue[dict] = queue.Queue()
    net = NetClient(config.endpoints, inbox, wake)
    store = Store()
    idle = IdleTimer(config.ambient_after_s)
    idle.idle = start_ambient
    lights: LightsClient | None = None
    if config.lights_room:
        lights = LightsClient(config.lights_room, inbox, wake)
        if not lights.configured:
            log.warning("lights_room is set but the DIRIGERA hub isn't paired; run scripts/dirigera_pair.py")
            lights = None
    backlight = Backlight() if config.fullscreen else None
    app = App(screen, store, net, Fonts(), idle, config.ambient_brightness,
              lights=lights, lights_title=config.lights_title or config.lights_room or "Lights",
              backlight=backlight, dark_after_s=config.dark_after_s,
              wol=WakeOnLan(config.wake_mac, config.wake_broadcast) if config.wake_mac else None,
              idle_quiet_s=config.ambient_after_quiet_s)
    if start_tab is not None:
        app.select_tab(start_tab)
    net.start()
    if lights is not None:
        lights.start()

    clock = pygame.time.Clock()
    started = time.monotonic()
    finger: int | None = None  # only the first finger down drives input
    running = True

    while running:
        events = pygame.event.get()
        if not events and not app.needs_draw():
            wait = app.wait_ms() if screenshot is None else min(app.wait_ms(), 200)
            events = [pygame.event.wait(wait)]

        for event in events:
            if event.type == pygame.QUIT or (event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE):
                running = False
            elif event.type == pygame.FINGERDOWN and finger is None:
                finger = event.finger_id
                app.pointer_down((event.x * theme.WIDTH, event.y * theme.HEIGHT))
            elif event.type == pygame.FINGERMOTION and event.finger_id == finger:
                app.pointer_move((event.x * theme.WIDTH, event.y * theme.HEIGHT))
            elif event.type == pygame.FINGERUP and event.finger_id == finger:
                finger = None
                app.pointer_up((event.x * theme.WIDTH, event.y * theme.HEIGHT))
            # SDL also synthesizes mouse events from touch; skip those. (labwc's mouseEmulation delivers
            # the touchscreen as a real pointer, so on the Pi input usually arrives through these.)
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and not event.touch:
                app.pointer_down(event.pos)
            elif event.type == pygame.MOUSEMOTION and event.buttons[0] and not event.touch:
                app.pointer_move(event.pos)
            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1 and not event.touch:
                app.pointer_up(event.pos)

        while True:
            try:
                message = inbox.get_nowait()
            except queue.Empty:
                break
            if store.apply(message):
                app.dirty = True

        app.tick()

        if app.needs_draw():
            app.draw()
            pygame.display.flip()
        elif rects := app.draw_frame():
            pygame.display.update(rects)

        if screenshot is not None and time.monotonic() - started >= screenshot_after_s:
            pygame.image.save(screen, str(screenshot))
            log.info("saved screenshot to %s", screenshot)
            running = False

        clock.tick(LENS_FPS if app.lens_moving else config.fps)

    if backlight is not None:
        backlight.set(True)  # never leave the screen dark when the deck stops
    net.stop()
    pygame.quit()
