# Pi Media Deck

A small touchscreen next to a Windows PC: a **Raspberry Pi Zero 2 W** with a **Pimoroni HyperPixel 4.0 Touch** (800×480) that controls the PC's audio and Spotify, plus a few things around it.

- **Volume**: master and per-app volume and mute, a Discord sidebar with mute/deafen during a call, and switching the Windows audio output.
- **Spotify**: album art, title (scrolls when long), artist, progress bar and play/pause/next/previous, read from the Windows media session (no Spotify account or API key).
- **Lights**: IKEA DIRIGERA lamps (on/off, brightness, warmth), talking to the hub directly.
- **Idle**: a dimmed album-cover view with a clock. When the PC is off the screen goes fully dark, and a **Turn on PC** button wakes it with Wake-on-LAN. A shut-down button (with confirmation) turns it off.

## How it works

```
Windows PC: pc-companion (C# .NET 8 tray app)  ◄── WebSocket :8765, JSON ──►  Pi: pi-deck (Python + pygame-ce)
```

- `pc-companion/`: tray app that reads Spotify through SMTC and audio through Core Audio (NAudio), and serves state over WebSocket. Autostarts with a Task Scheduler logon task.
- `pi-deck/`: fullscreen pygame-ce UI, a systemd user service. Includes a polling touch daemon for the HyperPixel's GT911 (its interrupt line floats on this board), a Wi-Fi watchdog, and the DIRIGERA client.
- `tools/deck_client.py`: interactive protocol test client.
- `docs/CONTEXT.md`: the full spec, protocol, architecture and the reasons behind decisions.

## Setup (short)

PC (needs the .NET 8 SDK):
```powershell
cd pc-companion
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```
Then use the tray menu's "Allow through firewall…".

Pi (Raspberry Pi OS with desktop, HyperPixel overlay `dtoverlay=vc4-kms-dpi-hyperpixel4`):
```bash
sudo apt install fonts-nanum   # Korean titles (the default fonts have no Hangul)
cd ~/pi-deck && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
mkdir -p ~/.config/pi-deck && cp config.example.toml ~/.config/pi-deck/config.toml   # set the PC's IP
sudo bash scripts/install-touch.sh
sudo bash scripts/install-wifi-watchdog.sh
bash scripts/install-autostart.sh
```
Optional: Discord (your own Discord application; see `docs/CONTEXT.md`), lights (`python3 scripts/dirigera_pair.py <hub-ip>`), Wake-on-LAN (`wake_mac` in the config, PC on Ethernet).

## Tests

```powershell
cd pi-deck
python -m unittest discover tests
```

Fonts: [Fredoka](https://fonts.google.com/specimen/Fredoka), SIL Open Font License (`pi-deck/fonts/OFL.txt`).
