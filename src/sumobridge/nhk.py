"""Client for NHK WORLD-JAPAN's public "shows" API.

The GRAND SUMO Highlights episode list lives at::

    https://api.nhkworld.jp/showsapi/v1/en/video_programs/sumo/video_episodes

It needs no authentication and returns the fifteen episodes of the current
tournament, each with an HLS master playlist URL. NHK expires episodes roughly
two weeks after they air, so the feed is a rolling window rather than an
archive.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone

import httpx

from .numbering import Numbering, NumberingError, resolve

log = logging.getLogger(__name__)

API_TEMPLATE = (
    "https://api.nhkworld.jp/showsapi/v1/{lang}/video_programs/{program}/video_episodes"
)
PROGRAM_ID = "sumo"
SITE_BASE = "https://www3.nhk.or.jp"

#: Bitrate of the top rendition NHK serves (3441k video + ~128k audio), used to
#: estimate release sizes. NHK does not publish a byte count.
_TOTAL_BITRATE = 3_569_000


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class Episode:
    """One GRAND SUMO Highlights episode, ready to be advertised to Sonarr."""

    nhk_id: str
    title: str
    description: str
    aired: datetime
    duration: int
    stream_url: str
    numbering: Numbering
    page_url: str
    thumbnail: str | None = None
    expires_at: datetime | None = None

    @property
    def season(self) -> int:
        return self.numbering.season

    @property
    def episode(self) -> int:
        return self.numbering.episode

    @property
    def size_bytes(self) -> int:
        """Estimated download size. Sonarr shows this and may apply size limits."""
        return int(self.duration * _TOTAL_BITRATE / 8) if self.duration else 0

    @property
    def expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= datetime.now(timezone.utc)


def _absolute(url: str | None) -> str | None:
    if not url:
        return None
    return url if url.startswith("http") else f"{SITE_BASE}{url}"


def parse_episode(
    item: dict, season_offsets: dict[int, int] | None = None
) -> Episode | None:
    """Convert one API item into an :class:`Episode`, or ``None`` if unusable."""
    nhk_id = item.get("id")
    video = item.get("video") or {}
    stream_url = video.get("url")
    if not nhk_id or not stream_url:
        return None

    aired = _parse_ts(item.get("first_broadcasted_at")) or _parse_ts(
        video.get("published_at")
    )
    if aired is None:
        log.warning("episode %s has no broadcast timestamp, skipping", nhk_id)
        return None

    title = item.get("title") or ""
    description = item.get("description") or ""
    try:
        numbering = resolve(title, description, aired)
    except NumberingError as exc:
        # Specials and one-off features share the feed with the daily episodes.
        # They have no TheTVDB episode entry, so there is nothing for Sonarr to
        # match them to.
        log.info("skipping episode %s (%s): %s", nhk_id, title, exc)
        return None

    offset = (season_offsets or {}).get(numbering.season)
    if offset:
        numbering = replace(numbering, episode=numbering.episode + offset)
        log.debug(
            "applied offset %+d to season %d, episode is now %d",
            offset,
            numbering.season,
            numbering.episode,
        )

    images = item.get("images") or []
    thumbnail = None
    if images:
        largest = max(images, key=lambda i: i.get("width") or 0)
        thumbnail = _absolute(largest.get("url"))

    return Episode(
        nhk_id=str(nhk_id),
        title=title,
        description=description,
        aired=aired,
        duration=int(video.get("duration") or 0),
        stream_url=stream_url,
        numbering=numbering,
        page_url=_absolute(item.get("url")) or f"{SITE_BASE}/nhkworld/en/shows/{nhk_id}/",
        thumbnail=thumbnail,
        expires_at=_parse_ts(video.get("expired_at")),
    )


class NhkClient:
    """Fetches and caches the episode listing."""

    def __init__(
        self,
        lang: str = "en",
        cache_ttl: int = 300,
        timeout: float = 20.0,
        season_offsets: dict[int, int] | None = None,
    ):
        self._url = API_TEMPLATE.format(lang=lang, program=PROGRAM_ID)
        self._cache_ttl = cache_ttl
        self._season_offsets = season_offsets or {}
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._cached: list[Episode] = []
        self._fetched_at: float = 0.0

    async def episodes(self, force: bool = False) -> list[Episode]:
        """Current episodes, newest first. Cached for ``cache_ttl`` seconds.

        On a fetch error the previous listing is served rather than an empty
        one, so a transient NHK outage does not make Sonarr think every episode
        vanished.
        """
        loop = asyncio.get_running_loop()
        async with self._lock:
            fresh = loop.time() - self._fetched_at < self._cache_ttl
            if self._cached and fresh and not force:
                return self._cached

            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(self._url)
                    response.raise_for_status()
                    payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("NHK listing fetch failed (%s); serving cache", exc)
                return self._cached

            episodes = [
                episode
                for item in payload.get("items", [])
                if (episode := parse_episode(item, self._season_offsets)) is not None
            ]
            episodes.sort(key=lambda e: e.aired, reverse=True)
            self._cached = episodes
            self._fetched_at = loop.time()
            log.info("fetched %d episodes from NHK", len(episodes))
            return episodes

    async def get(self, nhk_id: str) -> Episode | None:
        for episode in await self.episodes():
            if episode.nhk_id == nhk_id:
                return episode
        return None
