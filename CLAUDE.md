# Pi Media Deck

A touchscreen media deck: a Raspberry Pi Zero 2 W + HyperPixel 4.0 Touch that controls
**per-app volume** and **Spotify playback** on a Windows PC.

Full functional spec, architecture and protocol: **[docs/CONTEXT.md](docs/CONTEXT.md)** — read it
before making design changes, and keep it up to date when decisions change.

## Repo layout

```
pc-companion/   C# .NET 8 Windows tray app (WebSocket server, reads/controls media + audio)
pi-deck/        Python 3 + pygame-ce UI that runs fullscreen on the Pi (WebSocket client); fonts/ = bundled Fredoka (OFL)
tools/          deck_client.py — interactive protocol test client (`.venv\Scripts\python tools\deck_client.py [ws://host:8765]`;
                PC venv at repo root: `python -m venv .venv; .venv\Scripts\pip install websockets`)
docs/           CONTEXT.md (spec, protocol, decisions)
```

## Hardware / environment

- Pi: Raspberry Pi Zero 2 W (512 MB RAM, quad-core A53, 2.4 GHz Wi-Fi only)
- Screen: Pimoroni HyperPixel 4.0 Touch, 800x480, DPI. Native orientation is portrait — used **rotated to landscape 800x480**.
  - Enabled via `dtoverlay=vc4-kms-dpi-hyperpixel4` in `/boot/firmware/config.txt`
  - Uses almost all GPIO; I2C must stay disabled. No other HATs.
  - Touch is polled over I2C by `pi-deck-touch.service` (`pi-deck/touchd/`), with the kernel goodix driver blacklisted: the GT911's IRQ line floats on this board. Install/update with `sudo bash ~/pi-deck/scripts/install-touch.sh` (see CONTEXT.md §4).
- Pi OS: Raspberry Pi OS **with desktop** (Wayland/labwc)
- SSH: `ssh <pi>` (public-key auth). `<pi>`, `<pi-host>`, `<hub-ip>` and this install's LAN addresses are in the git-ignored **`CLAUDE.local.md`** — the repo is public, so never commit them.
- PC: Windows 10/11, Spotify desktop app
- Lights: IKEA DIRIGERA hub at `<hub-ip>` (household's); the Pi talks to it directly. Pair with `python3 ~/pi-deck/scripts/dirigera_pair.py <hub-ip>` + hub button; credentials in `~/.config/pi-deck/dirigera.json` on the Pi — never print or copy the token (see CONTEXT.md "Lights").

## Commands

PC companion (from `pc-companion/`). It's a tray app (`WinExe`): no console output, logs go to `%LOCALAPPDATA%\PiMediaDeck\pc-companion.log`. Only one instance runs at a time (named mutex), so exit the installed one from the tray before `dotnet run`.
```powershell
dotnet build
dotnet run
powershell -ExecutionPolicy Bypass -File scripts\install.ps1   # publish to %LOCALAPPDATA%\Programs\PiMediaDeck, autostart, start
```

Pi UI — deploy (from repo root, on the PC). The deck runs as the systemd user service `pi-deck` (autostarts with the desktop; set up once with `bash ~/pi-deck/scripts/install-autostart.sh` on the Pi):
```powershell
scp -r pi-deck <pi>:~/
ssh <pi> "systemctl --user restart pi-deck"
ssh <pi> "journalctl --user -u pi-deck -n 30"
```
To run it by hand (e.g. with `--screenshot`), first `systemctl --user stop pi-deck`.
Pi UI on the PC (dev) and tests (from `pi-deck/`, using the repo-root `.venv` with pygame-ce + websockets):
```powershell
..\.venv\Scripts\python -m deck --windowed --endpoint ws://localhost:8765 [--tab volume | --ambient] [--screenshot out.png --after 3]
..\.venv\Scripts\python -m unittest discover tests
```
First-time setup on the Pi:
```bash
cd ~/pi-deck && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Conventions

- C#: .NET 8, `net8.0-windows10.0.19041.0` TFM, nullable enabled, file-scoped namespaces, async/await throughout.
- Python: 3.11+, type hints, pygame-ce, one asyncio/websocket thread + the pygame main loop, communicating via queues. Never block the render loop on network I/O.
- Protocol changes: update `docs/CONTEXT.md` **and** both sides in the same change.
- Keep the Pi side light: target < 100 MB RSS, ~30 FPS max, idle redraws only when something changes.
- Prefer small, testable steps; follow the phase plan in `docs/CONTEXT.md`.

## Gotchas

- Pi Zero 2 W is slow: `pip install` and `apt` take minutes. Prefer wheels; avoid heavy deps (no numpy/Qt unless needed).
- Windows Firewall must allow inbound TCP 8765. This PC's Wi-Fi is a **Public** network, so the rule covers Private and Public (local subnet only); add it from the tray menu ("Allow through firewall…").
- Spotify's SMTC timeline updates are infrequent — the Pi interpolates the progress bar locally.
- HyperPixel backlight stays on after Pi shutdown; that's hardware, not a bug.
- The Pi's Wi-Fi has dropped out at times (power save is now off; a watchdog timer reconnects it, and the journal is persistent: `journalctl -b -1` after a power cycle, see CONTEXT.md §4); `<pi-host>` may briefly not resolve; then use the Pi's IP with `-o HostKeyAlias=<pi-host>` to keep host-key checking. The PC is on Ethernet (its Wi-Fi address drops while the cable is in).
- Claude Code's PowerShell on this PC is **sandboxed**: writes to the user registry (HKCU) and some profile files can land in an overlay that Windows itself never sees (that's why the Run-key autostart "worked" but never ran). Anything that must take effect for real — autostart, system settings — goes through a service (Task Scheduler, netsh via UAC) or is run by the user. Processes started from the sandbox also see the overlay, so their logs can differ from the real app's.
- From Windows PowerShell 5.1, don't pass quoted shell code to `ssh` (inner quotes get stripped) or pipe scripts into it (adds BOM/CRLF). `scp` a script to the Pi and run `ssh host bash /tmp/script.sh`.
