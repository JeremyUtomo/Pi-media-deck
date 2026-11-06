# Pi Media Deck — Project Context & Spec

Living document. Update it when a decision changes.

## 1. Goal

A small dedicated touchscreen next to the PC that:

1. **Volume tab** — a mixer: master volume plus per-application volume and mute (Spotify, Discord, browser, games…).
2. **Spotify tab** — now playing: album cover, song title, artist, album, song length with a progress bar, and media buttons (previous / play-pause / next).

## 2. Decisions so far

| Topic | Decision |
|---|---|
| What it controls | Per-app + master volume on the Windows PC, and Spotify playback |
| Spotify integration | **Windows media session (SMTC)** via the PC companion app. No Spotify Web API, no account setup. Only works when Spotify desktop runs on this PC. |
| Connection | **Both** USB and Wi-Fi: USB (gadget networking) when plugged in, Wi-Fi as fallback |
| Pi UI stack | **Python + pygame-ce**, fullscreen |
| Pi OS | Raspberry Pi OS **with desktop** (Wayland) |
| Orientation | **Landscape 800x480** (HyperPixel rotated from native portrait) |
| PC app | **Tray app, autostart** with Windows, tray icon shows connection status |

## 3. Hardware

- Raspberry Pi Zero 2 W — 512 MB RAM, 2.4 GHz Wi-Fi only, micro-USB `PWR IN` + micro-USB `USB` (data/OTG).
- Pimoroni HyperPixel 4.0 Touch — 800x480 IPS, capacitive multi-touch, DPI interface.
  - `dtoverlay=vc4-kms-dpi-hyperpixel4` in `/boot/firmware/config.txt`.
  - Rotation via Control Centre → Screens (touch rotates with it, may need a reboot).
  - Uses nearly all GPIO; I2C disabled. Backlight stays on after shutdown.
- SSH key auth. This install's hostnames, user and LAN addresses are kept out of the repo, in the git-ignored `CLAUDE.local.md`.
- OS: Raspberry Pi OS based on **Debian 13 (trixie)**, aarch64, **Python 3.13**. Venv at `~/pi-deck/.venv` (websockets 17.4 installed).
- Network: Pi on Wi-Fi, PC on the same /24 LAN (now wired, see §7). No USB network interface on the Pi yet (USB cable is power only until phase 7).

## 4. Architecture

```
┌──────────── Windows PC ────────────┐        ┌──────── Pi Zero 2 W ────────┐
│ pc-companion (C# tray app)         │        │ pi-deck (Python + pygame)   │
│  ├─ MediaService  (SMTC)           │  WS    │  ├─ net thread (websockets) │
│  ├─ AudioService  (Core Audio)     │◄──────►│  ├─ state store             │
│  └─ WebSocket server :8765         │ JSON   │  └─ UI: tabs Volume/Spotify │
└────────────────────────────────────┘        └─────────────────────────────┘
```

- **PC is the server**, Pi is the client. The Pi tries endpoints in order (USB address first, then Wi-Fi host) and reconnects automatically with backoff.
- PC pushes state; Pi sends commands. The PC echoes the resulting state, so the Pi never assumes a command succeeded.

### PC companion (`pc-companion/`)
- .NET 8, `net8.0-windows10.0.19041.0`, WinForms `NotifyIcon` for the tray (no main window).
- **Media**: `Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager`. Pick the session whose `SourceAppUserModelId` contains `Spotify` (desktop `Spotify.exe` or Store `SpotifyAB.SpotifyMusic_*`). Subscribe to `MediaPropertiesChanged`, `PlaybackInfoChanged`, `TimelinePropertiesChanged`.
- **Album art**: read the `Thumbnail` stream, resize to 300x300 JPEG (quality ~85), send only when it changes (hash).
- **Album art** is only re-read when the track changes; a missing thumbnail is retried (Spotify often publishes it after the title). While paused, Spotify re-publishes the same position with a new timestamp — the PC suppresses those so the Pi doesn't redraw.
- **Audio**: Core Audio via **NAudio** (`MMDeviceEnumerator`, `AudioSessionManager`, `SimpleAudioVolume`, `AudioEndpointVolume`). Group sessions by process; resolve display name (exe `FileDescription`) and a 64x64 PNG icon from the process exe, cached per PID. Changes made elsewhere (other apps, Windows mixer, new/closed sessions) are detected by **polling every 250 ms** and pushed only when the state differs — simpler and more robust than per-session COM event callbacks, and cheap in practice. Commands trigger an immediate re-read so the echo is fast.
- **Server**: Kestrel (ASP.NET Core minimal, WebSockets) on `0.0.0.0:8765`, path `/` — no URL ACL / admin needed. Multiple decks may connect; each has its own send queue, and icons are sent to each client once.
- **Commands run on one worker, never in a receive loop** (`DeckHub.RunCommandsAsync`, bounded queue of 128, drop-oldest). Why: Core Audio calls can hang for minutes. On 6 Nov 2026 an output switch stalled ~20 min; the receive loop stopped answering WebSocket pings, so the deck timed out and reconnected over and over. Later commands queued behind the lock, and at the end three stale switches ran in the same second. On 4 Nov `SetDefaultEndpoint` threw `0x800706BA` "RPC server is unavailable" while the audio service restarted, likely the Focusrite driver. Now the receive loop answers `ping` itself and only queues other commands. Commands that waited > 10 s are skipped and logged, commands still running after 3 s are logged, and any exception is logged without touching the connection. `SetOutput` no longer holds the audio lock.
- **Tray** (`Tray/TrayApp.cs`, WinForms `WinExe`): icon drawn at runtime with a green (deck connected) or grey (waiting) dot; tooltip/status line names the connected deck IP. Menu: status, "Start with Windows" (a per-user **Task Scheduler logon task** `PiMediaDeck`: logon trigger for the current user, 5 s delay, interactive token, least privilege, no time limit; created/deleted with `schtasks /XML`; checked only if the task runs *this* exe. Leftover HKCU `Run`/`StartupApproved` values are removed.)
  - Why not the Run key: autostart failed twice. Root cause found later: the Run value had been written by `install.ps1` from Claude Code's **sandboxed shell**, whose registry/profile writes go to an overlay — the real Run key never had the entry, so Explorer never saw it. A scheduled task is registered through the Task Scheduler service, so it is real even when created from that shell (verified: `schtasks /Run` started the app in the real desktop session and the Pi connected to it)., "Allow through firewall…" (shown only while the rule is missing), "Open log", Exit. Single instance via a named mutex; a port-in-use error is shown in a message box.
- Threading: the UI thread is STA; `MediaService`/`AudioService` are created on a thread-pool (MTA) thread because Kestrel and the audio poll use their COM/WinRT objects from other threads.
- Log: `%LOCALAPPDATA%\PiMediaDeck\pc-companion.log` (UTF-8 with BOM, rolls to `.old` past 1 MB at startup). Media changes are logged only when the track or play state changes.
- Install: `pc-companion/scripts/install.ps1` stops the running app (via the task, then by process), publishes Release to `%LOCALAPPDATA%\Programs\PiMediaDeck\`, registers the logon task and starts the app **through the task**, so it runs in the real desktop session (no admin). From Claude Code's sandboxed shell the task-started process can't be stopped, so run the install outside the sandbox. Re-run to update. Framework-dependent (needs the .NET 8 + ASP.NET Core runtimes, which the SDK includes).
- Firewall: rule **"Pi Media Deck (TCP 8765)"**, inbound TCP 8765, `remoteip=localsubnet`, profiles **Private and Public** (this PC's Wi-Fi is classified Public), added via an elevated `netsh` from the tray menu (UAC prompt). Independent of the exe path. Windows also auto-creates per-program allow rules when you accept its first-run prompt for a new exe path.

### Pi UI (`pi-deck/`)
- Python 3.11+, `pygame-ce`, `websockets`. Fullscreen 800x480 window under the desktop session.
- Network runs in its own thread (asyncio loop) and talks to the pygame loop through queues.
- Touch: handle `FINGERDOWN/MOTION/UP` and real mouse events (SDL's touch-synthesized mouse events are skipped). labwc's `mouseEmulation="yes"` delivers the touchscreen as a pointer, so on the Pi input normally arrives as mouse events. Slider drags send at most one command per 50 ms; the final value is always sent on release.
- Gestures (App → `Tab` base class in `deck/ui/tab.py`): tabs return hit regions each frame. A press that doesn't move 10 px is a tap; on a draggable region, movement turns into a vertical drag (sliders) or a horizontal scroll (mixer swipe), decided by the dominant axis. Tapping a slider jumps to that value. While dragging, the slider shows the local value (held 1 s after release) so the PC's echo doesn't make it jitter.
- Volume tab: strips of 132 px (6 fit on screen: master + 5 apps), master first with a divider. Each strip: 48 px icon (letter fallback when `iconHash` is null), name, 192 px slider, percentage, mute button (red when muted). More apps scroll horizontally, snapping to strips, with a scrollbar.
- Progress bar interpolates locally: `position = positionMs + (now - positionUpdatedAt)` while playing.
- Autostart: **systemd user service** `pi-deck.service` (`pi-deck/systemd/`, installed to `~/.config/systemd/user/` by `scripts/install-autostart.sh`, no sudo), `WantedBy=default.target`, `Restart=always`. The user manager already carries `WAYLAND_DISPLAY`; at boot the deck waits up to 120 s for the Wayland socket, then 5 s more so kanshi's rotation is applied before it goes fullscreen. Logs: `journalctl --user -u pi-deck`. The cursor is hidden while fullscreen.
- **No screen sleep — ambient view instead.** The Pi will be powered from the PC's USB, so it's only on while the PC is. After `ambient_after_s` (default 120) without touch, the deck shows a **clock** (24 h, the Pi's local time, Fredoka; redrawn only when the minute changes) above the current album cover at its native 300×300 — or, with nothing playing / disconnected, just a large centred clock — (drawn 1:1, centered; upscaling to 400 looked pixelated) over a blurred, darker copy of it filling the screen, with "title — artist" below (`deck/ui/ambient.py`, `deck/idle.py`). The HyperPixel backlight is on/off only (`/sys/class/backlight/backlight`, max 1), so the lower brightness is done in software: the image is multiplied by `ambient_brightness` (default 0.6; background ×0.35 more). The blur is the cover shrunk to 64×64 and scaled back up; building the layers takes ~26 ms on the Zero, once per track. Any touch returns to the normal view and is otherwise ignored. No progress-bar redraws while ambient. Switching is a **crossfade** (`App._start_fade`): a snapshot of the current screen is blended into a pre-rendered snapshot of the next state with smoothstep easing — 1.0 s into ambient, 0.35 s back out; ~8 ms per blended frame on the Zero, so it runs at the 30 fps cap; the title marquee pauses meanwhile. `--ambient` starts in it (for screenshots).
- **Dark when the PC is off.** It turned out the Pi stays powered while the PC is off. When the deck is idle (ambient) **and** the PC has been unreachable for `dark_after_s` (default 30; 0 = never; the grace period rides out Wi-Fi blips), it fades to black and then switches the **backlight off** (`deck/backlight.py`, writes 0/max to `/sys/class/backlight/*/brightness`, writable by group `video`). A touch lights it straight away and fades in the normal view ("Connecting to PC…"); idle again with the PC still gone → dark again. The PC coming back lights the ambient view again by itself. The backlight is switched on at start and when the deck exits, so a stopped deck never leaves the screen black. Only in fullscreen (not `--windowed` on the PC). Touch keeps working while dark (the touch daemon doesn't depend on the backlight).
- **Quick idle when there's nothing to control**: while the PC is disconnected **and** no reachable lamp is on (or there are no lights), idle comes after `ambient_after_quiet_s` (default 5 s) instead of 120 s (`App._idle_wait`, passed to `IdleTimer.update`). Not while "Starting the PC…" is showing. Turning a light on, or the PC connecting, brings back the normal timeout. Combined with the dark mode, a touch while the PC is long gone shows the deck for ~5 s, then it fades straight back to black.
- **Turn on PC (Wake-on-LAN).** With `wake_mac` set, the screen shown on PC tabs while disconnected reads "The PC is off" with a **Turn on PC** button (`deck/wol.py`): a magic packet (6×FF + MAC×16, UDP port 9, sent 3×) to `wake_broadcast` (the LAN's broadcast address, e.g. `192.168.1.255`), from the Pi's Wi-Fi. Then "Starting the PC…" with "Send again" for up to 120 s, or until the companion connects. No PC side involved (it's off).
  - PC: MSI B550M PRO-VDH WIFI desktop, S3 sleep (no Modern Standby), Fast Startup on. **Only the wired Realtek NIC can wake it from shutdown** (its MAC is `wake_mac`; "Shutdown Wake-On-Lan" and "Wake on Magic Packet" already enabled in its driver). The RZ616 Wi-Fi card is powered off in S5, so an Ethernet cable from the PC to the router is required. BIOS: ErP Ready **off** (also what keeps USB power on, i.e. the Pi running, while the PC is off) and **Resume By PCI-E Device on** (Settings → Advanced → Wake Up Event Setup; it was off by default, and that's why the first attempt failed). ✅ Verified Nov 2026: shut down from the deck's power button, woken with Turn on PC. Fast Startup didn't matter (`shutdown /s` does a full shutdown; the next boot was a cold boot, type 0x0).
  - The USB cable can't wake it: USB remote wake-up only works from sleep, not shutdown.
  - Powering from PC USB means the Pi loses power abruptly when the PC shuts down; consider a read-only root (Raspberry Pi OS overlay file system) if the SD card ever gets corrupted.
- Config file `~/.config/pi-deck/config.toml`: endpoint list, brightness/sleep options. Template: `pi-deck/config.example.toml`.
- Rendering: redraw only when state changes or a press is shown; while a track plays, the active Spotify tab redraws 4×/s for the progress bar. Rendered text is cached (font rendering is slow on the Zero). The UI loop sleeps in `pygame.event.wait`; the net thread posts a wake event after each message.
- Fonts: see §5 "Font". System fonts on the Pi: DejaVu Sans (+ Bold), **Nanum Gothic** (+ Bold) for Hangul (`sudo apt install fonts-nanum`, 33 MB), Droid Sans Fallback for kanji/kana. On Windows (dev runs) Segoe UI / Malgun Gothic.
- Display: when started from SSH, the deck attaches to the desktop's `wayland-0` itself and asks SDL for `wayland,x11`.
- Dev/test flags: `python -m deck --windowed --endpoint ws://localhost:8765` runs the UI on the PC; `--screenshot out.png --after 5` saves a frame and exits.
- **Touch: polled, not interrupt-driven.** The HyperPixel 4 overlay (`i2c@0/ft6236@5d|14`, `goodix,gt911`, bit-banged I2C bus 11 on GPIO 10/11, delay 4 µs) gives the GT911 an interrupt on GPIO 27 (falling edge, shared with the panel's SPI `sck`) but **no `irq-gpios`/`reset-gpios`**, so the goodix driver can't do the GT911 reset + INT handshake. The chip comes up in a random state — I2C address 0x5d or 0x14 — and the INT line floats.
  - Symptoms with the kernel driver: thousands of spurious IRQs/s with no touch data; `irq/184-gt911` at 24–40 % of a core; or `irq 184: nobody cared … Disabling IRQ #184` and touch dies until reboot.
  - GPIO pulls only partly help and depend on the latched address (at 0x14: pull-up → 0 IRQs; at 0x5d: pull-up ~20k/s, none ~4k/s, pull-down 300–5,300/s). `gpio=27=pu` in config.txt doesn't survive the driver binding (the line is still there, harmless). Goodix has no polling mode.
  - **Fix: `pi-deck-touch.service`** (root) runs `pi-deck/touchd/gt911_touchd.py` (installed to `/usr/local/lib/pi-deck/`, stdlib only): blacklists `goodix_ts` (`/etc/modprobe.d/pi-deck-no-goodix.conf`), probes 0x5d/0x14 for product id `911`, polls status register 0x814E over `/dev/i2c-11` (25 Hz idle, 60 Hz for 0.5 s after a contact), reads points from 0x814F, clears status, and publishes a uinput multitouch touchscreen **"pi-deck GT911 Touchscreen"** with the device tree's size/invert/swap applied exactly like the kernel's touchscreen helpers. Measured I2C cost: 0.67 ms per status read (~1.7 % of a core idle), 1.29 ms with one point. Verified after a reboot: service starts on its own, goodix stays unloaded, no touch IRQ, daemon at 1.4–3.3 % of a core idle (8 % in the first minute after boot), ~7–15 MB RSS. Fast polling is only extended by frames that contain a touch (the chip also sends empty "ready" frames).
  - Install/update: `sudo bash ~/pi-deck/scripts/install-touch.sh`. labwc maps the new device name (and the old goodix names, harmless) to `DPI-1` in `~/.config/labwc/rc.xml`.
- Rotation: `~/.config/kanshi/config` sets `DPI-1` transform 90 at login (kanshi is started by the system labwc autostart). Screen blanking is off (no swayidle).
- Wi-Fi power saving is **off** (`/etc/NetworkManager/conf.d/wifi-powersave-off.conf`, `wifi.powersave = 2`) after repeated dropouts.
- The Pi still fell off Wi-Fi completely once (Nov 2026; power was fine, `get_throttled=0x0`; cause unknown because the journal wasn't persistent). Since then:
  - **Wi-Fi watchdog**: `pi-deck-wifi-watchdog.timer` runs `wifi-watchdog.sh` as root every minute. It pings the wlan0 default gateway (the router). On failure it runs `nmcli device reconnect wlan0`; every 3rd failure in a row it also reloads `brcmfmac`. It never reboots. Logs: `journalctl -u pi-deck-wifi-watchdog`.
  - **Persistent journal** (`/etc/systemd/journald.conf.d/pi-deck-persistent.conf`, capped at 64 MB), so a dropout can be read after a power cycle with `journalctl -b -1`.
  - Install/update both: `sudo bash ~/pi-deck/scripts/install-wifi-watchdog.sh` (from the PC: `ssh -t <user>@<pi-host> "sudo bash …"`).

### Discord (`pc-companion/Discord/`)
- Discord's **local RPC** over the named pipe `\\.\pipe\discord-ipc-0..9` (`RpcConnection`: frames `[op int32][len int32][json]`, handshake → READY, commands matched by nonce, events as `DISPATCH`).
- Needs the user's **own Discord application** (discord.com/developers → New Application; OAuth2: copy Client ID, reset/copy Client Secret, add redirect `http://localhost`). The user writes `%LOCALAPPDATA%\PiMediaDeck\discord.json` = `{"clientId": "…", "clientSecret": "…"}` themselves (rechecked every 30 s). First connect: `AUTHORIZE` (scopes `rpc`, `rpc.voice.read`, `rpc.voice.write`) shows a prompt in Discord; the code is exchanged at `https://discord.com/api/v10/oauth2/token`; tokens live in `discord-token.json` (same folder) and are refreshed when < 1 h from expiry. A declined prompt isn't repeated for 10 min.
- State: `GET_VOICE_SETTINGS` + `SUBSCRIBE VOICE_SETTINGS_UPDATE` (mute/deaf), `GET_SELECTED_VOICE_CHANNEL` + `SUBSCRIBE VOICE_CHANNEL_SELECT` (in call + channel name). Toggles: `SET_VOICE_SETTINGS {mute|deaf}` from the current state. Reconnects every 5 s while Discord isn't running.
- Deck: while `status == "ready"` and `inCall`, the Volume tab shows a 176 px **Discord sidebar** on the right (Discord icon from its audio session, "In call", channel name without emoji — no emoji font on the Pi) with big **Mute** and **Deafen** buttons (warm red with a crossed-out mic/headphones when active). The mixer gets the remaining width and scrolls.
- Verified on the device: authorized once, sidebar appears in a voice call, Mute/Deafen toggle Discord and follow changes made in Discord.

### Lights (IKEA DIRIGERA, `pi-deck/deck/lights.py`)
- The house's IKEA lights (Zigbee) are on an **IKEA DIRIGERA hub** (shared with the household; also in Apple Home as a bridge). The deck talks to the hub **directly over the LAN** — no PC involvement, so the Lights tab works while the PC is off.
- Pairing: `python3 ~/pi-deck/scripts/dirigera_pair.py <hub-ip>`, then press the hub's action button. Writes `~/.config/pi-deck/dirigera.json` (mode 600: host, token, hub certificate SHA-256). The token is never printed. The hub's certificate is self-signed, so its fingerprint is **pinned** and checked on every connection before the token is sent.
- API: `GET /v1/devices` (filtered to `deviceType == "light"` in the configured room), `PATCH /v1/devices/{id}` with `[{"attributes": {name: value}}]` — one attribute per request, `isOn` first; live state from the event WebSocket `wss://hub:8443/v1` (`deviceStateChanged`). Commands are queued and coalesced per lamp/attribute (latest wins); the tab throttles slider sends to one per 150 ms.
  - **All lamps change together**: per attribute, the PATCHes for every lamp go out in parallel (one pooled connection each), `isOn` for all lamps first, then the rest. REST calls reuse **keep-alive HTTPS connections** from a small pool: a new TLS handshake to the hub costs ~380 ms on the Zero, a reused connection ~70 ms (measured). A pooled connection the hub has closed is replaced once transparently. (Before this, lamps switched one after another, >1 s apart.)
- The lamps it was built for: one room on the hub, **KAJPLATS 1–3** (GU10 WS 575 lm): on/off, brightness 1–100 %, colour temperature 2202–6535 K. They're white spectrum — **no colour/hue**. The hub reports `colorTemperatureMin`/`Max` swapped (min = coolest); `parse_lamp` normalises that. If colour bulbs (`colorHue` in `canReceive`) are added later, `can_color` is already parsed.
- Config: `lights_room` (the hub's room name, case-insensitive) and an optional `lights_title` in `config.toml`; without `lights_room` there's no Lights tab or quick toggle.
- Cost: the deck's RSS went from ~83 MB to ~98 MB with the second (TLS) network thread — close to the 100 MB target.
- **Memory investigation (Nov 2026)**: a fresh deck is ~109 MB RSS. ~55 MB is Mesa GL (`libLLVM` alone 41 MB, then partly swapped out; `libgallium` 11 MB): SDL2's Wayland backend has no framebuffer path (`SDL_FRAMEBUFFER_ACCELERATION=0` → "Window framebuffer support not available"), so it always brings up GLES, and Mesa's gallium megadriver links LLVM even for the hardware `vc4` driver. softpipe / forcing vc4 change nothing; software GL is worse (128 MB); `MALLOC_ARENA_MAX=2` doesn't help. Python + imports ≈ 29 MB; the deck's own heap ≈ 15–20 MB. The only real fix would be presenting via Wayland `wl_shm` directly (pure-Python Wayland client, ~50 MB saved) — not done; ~147 MB stays available on the Pi.
- Verified on the device: Lights tab and quick toggle control the KAJPLATS lamps.

## 5. Screens (800x480 landscape)

- **Look**: warm and creamy, light theme (`deck/ui/theme.py`): latte-cream background (226,211,188 — the first paper-white cream looked too white on the panel), slightly darker cream cards, cocoa-brown text, one terracotta accent everywhere (play button, progress bar, all mixer sliders, master icon), sage only for the "connected" dot, warm red for muted. Knobs are cream with an accent ring (grey ring when muted). The ambient view stays dark with its own cream text (`AMBIENT_TEXT`). Rounded everything: pill tabs and status badge, rounded cards per mixer strip, pill mute buttons, round transport buttons, album art with anti-aliased rounded corners (`widgets.rounded`, composited onto the background once per track so it blits as an opaque surface).
- **Font**: **Fredoka** (SIL OFL, `pi-deck/fonts/` with `OFL.txt`), Medium for text and SemiBold for bold, cut from the Google Fonts variable font with fontTools on the PC (`fonttools varLib.instancer … wght=500|600 wdth=100`; pygame can't pick variable-font weights). Fredoka is Latin-only, so text is rendered in **per-character runs** (`Fonts.runs`): each character uses the first font in the chain whose cmap has it — Fredoka → DejaVu Sans (symbols like ♪) → Nanum Gothic (Hangul) → Droid Sans Fallback (kanji/kana, last resort) — and the runs are blitted on one baseline (spaces stay with the run before them). Whole-string fallback broke mixed titles: Droid Sans Fallback has **no Latin letters**, so "抱かれに来た女 — Kingo Hamada" lost the artist name. Droid Sans Fallback also has **no Hangul syllables**, so Korean titles ("추리소설 (Detective Soseol)") were empty boxes on the Pi until Nanum Gothic was added (Nov 2026); `test_korean_gets_a_font_that_has_it` checks the chosen font really has them. Ellipsizing measures across runs. Coverage comes from a small stdlib cmap parser (`theme._codepoints`), because pygame's `Font.metrics()` reports the placeholder glyph for missing characters instead of None.
- **Scrolling title**: a song title wider than its space pauses 2.5 s, scrolls at 40 px/s to its end plus a gap, and loops seamlessly, with soft fades at both edges. Only the title strip is redrawn (`Tab.draw_frame` + `pygame.display.update(rect)`, every 50 ms): 0.25 ms per frame on the Zero vs 3.3 ms for a full redraw. A new title restarts the pause. Artist/album still ellipsize.

- **Lights tab** (`deck/ui/lights.py`, works without the PC): the room's lamps are always controlled **as one** (no per-lamp controls). Left card = room title, "On · 57%" / "Off", big round power button (all lamps; off if any is on). Right card = brightness slider with 25/50/75/100 % presets, warmth slider (warm→cool gradient) with Warm 2202 / Soft 2700 / Neutral 4000 / Cool 6500 K presets; presets switch the lamps on and highlight when they match. Horizontal sliders use `Hit(horizontal=True)` so sideways drags move them instead of scrolling. Dragged values are shown locally until the hub's events catch up.
- **PC power button** (`deck/ui/power.py`): a small neutral round button in the tab bar, left of the quick light toggle, shown only while the PC is connected. Tapping it opens a centred confirmation dialog ("Shut down the PC?", warning about unsaved work, Cancel / red Shut down); tapping outside or on Cancel closes it, taps on the dialog body do nothing. Only "Shut down" sends `pc.power`, and the PC runs `shutdown /s /t 5` (Windows' own 5 s notice; `shutdown /a` aborts). Never triggered during development.
- **Quick light toggle**: a round bulb button in the tab bar on every tab, left of the output pill — filled terracotta when any lamp is on; tap = all on/off. Hidden while the hub is unavailable.
- **Glass tab bar** (Nov 2026, in the spirit of Apple's Liquid Glass; `deck/ui/glass.py`):
  - **Look:** the bar is one floating capsule (`theme.BAR_*`: 8 px in from the sides, y 6–58). It has a blurred backdrop (the region behind it, smoothscaled ÷10 and back), a warm translucent cream tint that's brighter at the top, a 1 px light rim (bright on top, faint below) and a soft cocoa drop shadow. The selected tab is a brighter glass **lens**. Power, light-off and output buttons are faint glass **chips**; pressed or open shows a darker lens. The light button is still solid terracotta when on.
  - **Gotcha:** clear SRCALPHA surfaces to transparent *white* (`glass.CLEAR`), not `(0, 0, 0, 0)`, before drawing and smoothscaling. smoothscale averages colour too, so black transparent pixels leave a grey dotted fringe on every curved edge.
- **Glass everywhere** (all tabs and popups, same module):
  - **Styles:** cards are `CARD`: frosted cream, a touch lighter than the page, with a rim and a soft shadow; the Spotify art placeholder uses it too. Buttons are clear `LENS`es, with `LENS_PRESSED` while pressed. Coloured buttons are **tinted glass**, `draw(..., color=…)` with `TINTED`: play, the selected preset, lights on, active mute/deafen, the master icon, Shut down and Turn on PC. That's a solid fill under the gloss and rim. Popups (output menu, shut-down dialog) are `POPUP` and really blur what's behind them (÷14, mostly opaque so text behind doesn't show through).
  - **Compositing:** for an unblurred style, shadow, tint, body and rim are combined once into one cached image **with premultiplied alpha** (`BLEND_PREMULTIPLIED`). pygame's plain blit of translucent onto translucent treats the bottom one as opaque, so the cocoa shadow bled into the frosted body and everything turned muddy grey. The shadow is also cut out under the shape, so it shows only around the glass.
  - **Backplates (`glass.Backplate`):** each tab, and the tab bar, lists its glass panels every frame. While the list is unchanged, all of them come from one cached opaque image; only sliders, knobs, icons and text are drawn on top. Only a layout or button-state change rebuilds it (scrolling the mixer, toggling mute, pressing a button); dragging a slider doesn't.
  - **Measured on the Zero** (full redraw, before glass → glass without backplates → now): Volume 9.9 → 31 → 8.4 ms, Spotify 4.1 → 13 → 5.8, Lights 7.5 → 26 → 6.9, output menu 14 → 36 → 17, shut-down dialog 16 → 44 → 25 (popups blur live, but only while open).
  - **No banding:** the HyperPixel's DPI link is **18-bit (6 bits per colour)**, so the panel shows only every 4th shade, and the glass gradients span ~5 shades. On the device the highlight's fade ended in a hard line across every widget at 55 % of its height, plus coarse bands. Fixes: the highlight eases out over the full height ((1 − t)², no slope break), and every backplate gets a 4×4 ordered (Bayer) dither of +0–3 per channel, aligned to screen coordinates (`glass.dither`), so the panel's dropped low bits average out. It costs one blit per backplate rebuild. Check banding by simulating the panel: `value & 0xFC`, then stretch the contrast.
  - **Cache building:** supersampling is 4× for shapes up to 200 px (2× left the 152 px light button fuzzy) and 2× above that. Building the caches still takes 0.2–0.7 s per tab, so `App._warm_next_tab` draws each unvisited tab off-screen once, as soon as its data is there and nobody is touching the screen, one tab per tick. Measured: one-off pauses of 0.26 and 0.55 s after the PC/hub first connect; the first visit to a tab is then 13–17 ms.
- **Tab bar**: large tabs (Volume, Spotify, Lights — 136 px each with three), finger-sized (≥ 60 px touch height), plus the **audio output pill** on the right (`deck/ui/outputs.py`): speaker icon + the current output's short name (the part before " (" in Windows' "endpoint (device)" name, capped at 80 px so the bar stays uncluttered) + chevron. Tapping it opens a popup listing the active playback devices (endpoint name, device underneath, check on the current one); picking one sends `audio.set_output` and closes it; tapping outside closes it. While open it takes all input and the title marquee pauses. When disconnected (or with an older server) the pill shows the connection state instead ("Connecting…" amber / "PC" sage).
  - PC side: `AudioService` lists active render endpoints each poll (names cached) and switches the default via the undocumented `IPolicyConfig` COM interface (`Audio/PolicyConfig.cs`, as used by EarTrumpet/SoundSwitch) for Console, Multimedia and Communications. The mixer follows automatically, since it always reads the default device. Verified on the device: switching between the Focusrite and the monitor speakers works.
- **Spotify tab**: album art ~300x300 on the left; title (large), artist, album on the right; progress bar with `elapsed / total`; prev / play-pause / next buttons. If Spotify isn't running: "Spotify not playing" placeholder.
- **Volume tab**: master strip first, then one vertical strip per app: icon, name, slider, mute button, percentage. Horizontal swipe when there are more apps than fit (~5–6 strips).

## 6. Protocol (WebSocket, JSON, port 8765)

Every message: `{ "type": "<name>", ...payload }`. Version 1.

**PC → Pi**
```jsonc
{ "type": "hello", "protocol": 1, "server": "pc-companion/0.1" }
{ "type": "media.state", "available": true, "app": "Spotify",
  "title": "…", "artist": "…", "album": "…",
  "durationMs": 215000, "positionMs": 42000, "positionUpdatedAt": 1730000000000,
  "isPlaying": true, "canPrevious": true, "canNext": true, "artHash": "ab12…" }
{ "type": "media.art", "hash": "ab12…", "mime": "image/jpeg", "data": "<base64>" }
{ "type": "audio.state",
  "master": { "volume": 0.62, "muted": false },
  "sessions": [ { "id": "spotify.exe", "name": "Spotify", "volume": 0.8, "muted": false, "iconHash": "cd34…" } ],
  "output": "{0.0.0.00000000}.{…}",                       // default playback device id
  "outputs": [ { "id": "{0.0.0.00000000}.{…}", "name": "Speakers (Realtek(R) Audio)" } ] }  // active playback devices
{ "type": "audio.icon", "hash": "cd34…", "mime": "image/png", "data": "<base64>" }
{ "type": "discord.state", "status": "ready", "inCall": true, "channel": "General",
  "muted": false, "deafened": false }   // status: not_running | needs_setup | authorizing | denied | ready
{ "type": "pong" }
```

**Pi → PC**
```jsonc
{ "type": "hello", "protocol": 1, "client": "pi-deck/0.1" }
{ "type": "media.command", "action": "play_pause" }   // play_pause | next | previous
{ "type": "audio.set_volume", "target": "master", "volume": 0.5 }   // target: "master" | session id
{ "type": "audio.set_mute", "target": "spotify.exe", "muted": true }
{ "type": "audio.set_output", "id": "{0.0.0.00000000}.{…}" }   // make it the Windows default (all roles)
{ "type": "discord.command", "action": "toggle_mute" }          // toggle_mute | toggle_deafen (PC flips the current state)
{ "type": "pc.power", "action": "shutdown" }                    // only after the deck's confirmation dialog
{ "type": "ping" }
```

Rules: on connect the PC sends `hello`, `media.art` (if any), `media.state`, then any `audio.icon`s followed by `audio.state`. Art and icons are always sent **before** the state that references their hash. Volumes are floats 0.0–1.0. Timestamps are Unix ms from the PC clock. The Pi caches art/icons by hash and only needs each one once. `iconHash` is `null` when no icon is available (e.g. `system` = "System sounds", elevated processes). Session ids are the lowercase exe name (`spotify.exe`); `system` is the system sounds session. Unknown or malformed messages are logged and ignored; the connection stays open. The server also sends WebSocket-level keep-alive pings every 15 s.

## 7. Connection: USB + Wi-Fi

- **Wi-Fi**: Pi connects to `<pc-hostname>.local:8765` or a fixed IP from config. The PC's hostname has an underscore, which makes `.local` resolution unreliable, so **use the IP** (from DHCP; a router reservation keeps it stable). Since Nov 2026 the PC is on **Ethernet** (for Wake-on-LAN), and Windows drops Wi-Fi while the cable is in, so the PC's address changed; the Pi's config lists the wired address, then the Wi-Fi one. Verified that the Pi's magic packets (sent from its Wi-Fi to the LAN broadcast address) arrive on the PC's wired NIC.
- **Firewall**: the PC's Wi-Fi profile is **Public**. Windows created program rules "pc-companion.exe — Allow inbound, Public" when the server first ran; verified working from the Pi. These rules are tied to the exe path, so a published/installed build in another folder will prompt again (phase 5 adds an explicit TCP 8765 rule).
- **USB**: Pi data port in USB gadget (Ethernet) mode — one cable for power + data. Static link addresses: PC `10.55.0.1`, Pi `10.55.0.2`. Windows driver support for the gadget (RNDIS/NCM) can need manual setup — this is the last phase, Wi-Fi first.
- Pi endpoint order: `10.55.0.1:8765` → Wi-Fi host. Reconnect with backoff (1 s → 10 s max).

## 8. Phase plan

1. **PC console prototype** — print Spotify SMTC info and the audio session list; change volume from the console. ✅ Done (`pc-companion/` console app; services in `Media/` and `Audio/` are reused by later phases).
2. **WebSocket server + protocol** — test with a small Python client on the PC. ✅ Done (server in `pc-companion/Server/`; interactive test client `tools/deck_client.py`). Verified end to end from the Pi over Wi-Fi.
3. **Pi Spotify tab** — connect, render now-playing + art + buttons. ✅ Done. Runs fullscreen on the Pi via Wayland against the live PC server (art, text, interpolated progress verified by screenshot); buttons verified by touch. Measured while playing: ~7 % CPU, 83 MB RSS (70 MB shared libs).
4. **Pi Volume tab** — mixer with sliders and mute. ✅ Done (`deck/ui/volume.py`); gesture tests pass headless on PC and Pi (`pi-deck/tests/`); sliders, mute and tab switching verified by touch on the device.
5. **PC tray app + autostart**, firewall rule. ✅ Done: installed to `%LOCALAPPDATA%\Programs\PiMediaDeck`, autostart registered, the Pi reconnects on its own after the app restarts. Port rule added from the tray menu and verified (`netsh` shows it: In, Allow, TCP 8765, LocalSubnet, Private+Public). Windows 11 hides new tray icons in the overflow (^) by default. Autostart failed at two restarts (Run entry only existed in a sandbox overlay, see Tray above); switched to a logon task — confirmed working after a restart.
6. **Pi autostart** — fullscreen on boot, cursor hidden, ambient album-cover view when idle. ✅ Autostart verified after a reboot (fullscreen ~64 s after power-on, connected at ~65 s); ambient view verified by screenshot on the Pi; 17 tests pass on PC and Pi. Pending: leaving the ambient view by touch on the device.
7. **USB gadget connection** with Wi-Fi fallback. 🚧 Pi side done: `sudo bash ~/pi-deck/scripts/install-usb.sh` ran `rpi-usb-gadget on` (dwc2 peripheral + g_ether, usb0 = 10.12.194.1/28, NetworkManager "shared" with DHCP for the PC) plus a dnsmasq snippet so the Pi never offers itself as the PC's gateway/DNS. The deck's first endpoint is `ws://usb:8765`: it probes usb0's subnet for the PC (skipped instantly while usb0 is down) and, while on Wi-Fi, checks every 15 s and switches back to USB once it answers. **Blocked on hardware**: Windows saw no USB device at all — the current cable is power-only or in PWR IN; a data cable in the Pi's "USB" port is needed. Then check Windows binds a driver to the gadget (VID 2E8A, PID 0013).

## 9. Open questions

- Spotify tab when Spotify isn't running: placeholder only, or fall back to whatever is playing (YouTube etc.)?
- Which apps to show in the mixer: all audio sessions, or a pinned/hidden list?
- ~~Screen sleep~~ — decided: no sleep (powered from the PC's USB); a dimmed album-cover ambient view after 2 min without touch (see §4). Revised: the Pi is in fact always powered, so idle + PC unreachable for 30 s → black screen, backlight off (see §4).
- Extra Spotify features later: seek by tapping the progress bar, shuffle/repeat, like button (likes would need the Web API).
