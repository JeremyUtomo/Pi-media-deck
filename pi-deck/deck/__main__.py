"""Entry point: `python -m deck` from the pi-deck folder."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

from deck import config

log = logging.getLogger("deck")


def _use_desktop_session(timeout_s: float = 120, settle_s: float = 5) -> None:
    """Attaches to the logged-in Wayland desktop, waiting for it when started at boot (systemd user service).

    Also lets the deck start from an SSH shell, where WAYLAND_DISPLAY isn't set.
    """
    if sys.platform != "linux" or (os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY")):
        return
    runtime = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
    socket = runtime / os.environ.get("WAYLAND_DISPLAY", "wayland-0")
    if not socket.exists():
        log.info("waiting for the desktop (%s)", socket)
        deadline = time.monotonic() + timeout_s
        while not socket.exists() and time.monotonic() < deadline:
            time.sleep(1)
        # The compositor's socket appears before kanshi applies the landscape rotation; give it a moment.
        time.sleep(settle_s)
    os.environ.setdefault("XDG_RUNTIME_DIR", str(runtime))
    os.environ["WAYLAND_DISPLAY"] = socket.name


def main() -> None:
    parser = argparse.ArgumentParser(prog="deck", description="Pi Media Deck UI")
    parser.add_argument("--config", type=Path, default=config.CONFIG_PATH, help="config.toml path")
    parser.add_argument("--endpoint", action="append", help="ws:// URL to use instead of the config (repeatable)")
    parser.add_argument("--windowed", action="store_true", help="run in a window instead of fullscreen")
    parser.add_argument("--screenshot", type=Path, help="save a PNG of the screen and exit (for testing)")
    parser.add_argument("--after", type=float, default=5.0, help="seconds before --screenshot is taken")
    parser.add_argument("--tab", choices=["volume", "spotify", "lights"], help="tab to show at start (default: spotify)")
    parser.add_argument("--ambient", action="store_true", help="start in the ambient (album cover) view")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    _use_desktop_session()
    if sys.platform == "linux" and os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("SDL_VIDEODRIVER", "wayland,x11")
    os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

    cfg = config.load(args.config)
    if args.endpoint:
        cfg = replace(cfg, endpoints=args.endpoint)
    if args.windowed:
        cfg = replace(cfg, fullscreen=False)

    from deck.app import run  # imports pygame, so after the SDL environment is set

    run(cfg, screenshot=args.screenshot, screenshot_after_s=args.after, start_tab=args.tab, start_ambient=args.ambient)


if __name__ == "__main__":
    main()
