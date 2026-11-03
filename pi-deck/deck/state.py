"""State store: applies messages from the net thread. Lives on the UI thread only."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from deck import lights, net

MAX_CACHED_IMAGES = 32


@dataclass(frozen=True)
class MediaState:
    available: bool
    title: str
    artist: str
    album: str
    duration_ms: int
    position_ms: int
    position_updated_at: int  # Unix ms, PC clock
    is_playing: bool
    can_previous: bool
    can_next: bool
    art_hash: str | None

    @classmethod
    def from_message(cls, m: dict) -> MediaState:
        return cls(
            available=bool(m.get("available")),
            title=m.get("title") or "",
            artist=m.get("artist") or "",
            album=m.get("album") or "",
            duration_ms=int(m.get("durationMs") or 0),
            position_ms=int(m.get("positionMs") or 0),
            position_updated_at=int(m.get("positionUpdatedAt") or 0),
            is_playing=bool(m.get("isPlaying")),
            can_previous=bool(m.get("canPrevious")),
            can_next=bool(m.get("canNext")),
            art_hash=m.get("artHash"),
        )

    def position_at(self, now_ms: int) -> int:
        """Interpolates the playhead locally; Spotify only reports the timeline every few seconds."""
        position = self.position_ms
        if self.is_playing:
            position += now_ms - self.position_updated_at
        upper = self.duration_ms if self.duration_ms > 0 else position
        return max(0, min(position, upper))


class ImageCache(OrderedDict[str, bytes]):
    """Raw image bytes by hash, oldest evicted first."""

    def put(self, key: str, data: bytes) -> None:
        self[key] = data
        self.move_to_end(key)
        while len(self) > MAX_CACHED_IMAGES:
            self.popitem(last=False)


class Store:
    def __init__(self) -> None:
        self.connected = False
        self.endpoint: str | None = None
        self.server: str | None = None
        self.media: MediaState | None = None
        self.art = ImageCache()
        self.audio: dict | None = None  # raw audio.state, used by the volume tab (phase 4)
        self.icons = ImageCache()
        self.lamps: list[dict] | None = None  # from the DIRIGERA hub (deck/lights.py); None while unavailable
        self.discord: dict | None = None  # raw discord.state from the PC

    def apply(self, message: dict) -> bool:
        """Applies one message. Returns True if anything visible may have changed."""
        match message.get("type"):
            case net.NET_CONNECTED:
                self.connected = True
                self.endpoint = message.get("endpoint")
            case net.NET_DISCONNECTED:
                self.connected = False
                self.server = None
            case "hello":
                self.server = message.get("server")
            case "media.state":
                self.media = MediaState.from_message(message)
            case "media.art":
                self.art.put(message["hash"], message["bytes"])
            case "audio.state":
                self.audio = message
            case "audio.icon":
                self.icons.put(message["hash"], message["bytes"])
            case "discord.state":
                self.discord = message
            case lights.LIGHTS_STATE:
                self.lamps = message["lamps"]
            case lights.LIGHTS_UNAVAILABLE:
                self.lamps = None
            case _:
                return False
        return True
