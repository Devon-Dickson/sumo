"""Runtime configuration, read from the environment."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

#: TheTVDB series id for "Grand Sumo Highlights". Used for the ``tvdbid``
#: attribute on Newznab items so Sonarr can match releases without relying on
#: the release title alone.
TVDB_SERIES_ID = 391618

#: Series title as Sonarr knows it (from TheTVDB). Release names are built from
#: this so Sonarr's parser resolves them to the right series.
SERIES_TITLE = "GRAND SUMO Highlights"


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_path(name: str, default: str) -> Path:
    return Path(_env(name, default)).expanduser()


@dataclass(frozen=True)
class Config:
    """All knobs for the bridge. Every field has a usable default."""

    # --- HTTP server -----------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8787
    #: Base URL Sonarr can reach this service on. Used to build the ``.nzb``
    #: enclosure links inside the Newznab feed, so it must be resolvable *from
    #: Sonarr*, not from the host running a browser.
    public_url: str = "http://localhost:8787"
    #: Shared secret for both the Newznab and the SABnzbd shim.
    api_key: str = field(default_factory=lambda: secrets.token_hex(16))
    #: True when no key was supplied and one was invented for this run, which
    #: means it changes on every restart and Sonarr will stop authenticating.
    api_key_was_generated: bool = False

    # --- NHK -------------------------------------------------------------
    lang: str = "en"
    #: How long the NHK episode listing is cached, in seconds. NHK publishes at
    #: most one new episode a day, so a few minutes is plenty.
    cache_ttl: int = 300
    #: yt-dlp format selector. NHK tops out at 720p and serves video and audio
    #: as separate HLS renditions, hence the explicit merge.
    video_format: str = "bestvideo[height<=?720]+bestaudio/best"

    # --- Download handling ----------------------------------------------
    #: Where in-progress downloads live. Must be on the same filesystem as
    #: ``complete_dir`` so the finished move is atomic.
    incomplete_dir: Path = Path("/downloads/incomplete")
    #: Root that Sonarr imports from. The category is appended to it.
    complete_dir: Path = Path("/downloads/complete")
    #: SABnzbd category Sonarr should be pointed at.
    category: str = "sumo"
    max_concurrent: int = 1
    #: Queue/history survive restarts by being written here.
    state_file: Path = Path("/config/state.json")

    # --- Cosmetics -------------------------------------------------------
    release_group: str = "SUMOBRIDGE"

    @classmethod
    def from_env(cls) -> Config:
        api_key = _env("SUMO_API_KEY")
        return cls(
            host=_env("SUMO_HOST", "0.0.0.0"),
            port=_env_int("SUMO_PORT", 8787),
            public_url=_env("SUMO_PUBLIC_URL", "http://localhost:8787").rstrip("/"),
            api_key=api_key or secrets.token_hex(16),
            api_key_was_generated=not api_key,
            lang=_env("SUMO_LANG", "en"),
            cache_ttl=_env_int("SUMO_CACHE_TTL", 300),
            video_format=_env(
                "SUMO_VIDEO_FORMAT", "bestvideo[height<=?720]+bestaudio/best"
            ),
            incomplete_dir=_env_path("SUMO_INCOMPLETE_DIR", "/downloads/incomplete"),
            complete_dir=_env_path("SUMO_COMPLETE_DIR", "/downloads/complete"),
            category=_env("SUMO_CATEGORY", "sumo"),
            max_concurrent=_env_int("SUMO_MAX_CONCURRENT", 1),
            state_file=_env_path("SUMO_STATE_FILE", "/config/state.json"),
            release_group=_env("SUMO_RELEASE_GROUP", "SUMOBRIDGE"),
        )

    @property
    def category_dir(self) -> Path:
        """Folder Sonarr ends up importing from."""
        return self.complete_dir / self.category
