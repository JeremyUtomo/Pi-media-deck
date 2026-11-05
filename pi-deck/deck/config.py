from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "pi-deck" / "config.toml"
DEFAULT_ENDPOINTS = ["ws://10.55.0.1:8765"]


@dataclass(frozen=True)
class Config:
    endpoints: list[str]
    fullscreen: bool = True
    fps: int = 30
    ambient_after_s: float = 120  # show the dimmed album cover after this long without touch; 0 = never
    ambient_after_quiet_s: float = 5  # the same, but while the PC is gone and no light is on; 0 = no shortcut
    ambient_brightness: float = 0.6  # 0.05-1.0; the backlight has no dimming, so the image is darkened
    dark_after_s: float = 30  # idle and the PC gone this long: screen and backlight off; 0 = never
    wake_mac: str | None = None  # the PC's wired Ethernet MAC: shows "Turn on PC" (Wake-on-LAN) while it's off
    wake_broadcast: str = "255.255.255.255"  # where the magic packet goes, e.g. the LAN's "192.168.1.255"
    lights_room: str | None = None  # DIRIGERA room whose lights get a tab and the quick toggle; None = no lights
    lights_title: str | None = None  # shown on the Lights tab; defaults to the room name


def load(path: Path = CONFIG_PATH) -> Config:
    data = tomllib.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return Config(
        endpoints=list(data.get("endpoints", DEFAULT_ENDPOINTS)),
        fullscreen=bool(data.get("fullscreen", True)),
        fps=int(data.get("fps", 30)),
        ambient_after_s=float(data.get("ambient_after_s", 120)),
        ambient_after_quiet_s=float(data.get("ambient_after_quiet_s", 5)),
        ambient_brightness=float(data.get("ambient_brightness", 0.6)),
        dark_after_s=float(data.get("dark_after_s", 30)),
        wake_mac=data.get("wake_mac"),
        wake_broadcast=str(data.get("wake_broadcast", "255.255.255.255")),
        lights_room=data.get("lights_room"),
        lights_title=data.get("lights_title"),
    )
