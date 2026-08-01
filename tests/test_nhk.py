"""Parsing of the real NHK payload (fixture captured from the live API)."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sumobridge.nhk import Episode, parse_episode

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures_nhk_episodes.json").read_text()
)


@pytest.fixture
def items() -> list[dict]:
    return FIXTURE["items"]


def test_parses_every_fixture_item(items):
    episodes = [parse_episode(item) for item in items]
    assert all(e is not None for e in episodes)


def test_maps_the_final_day_onto_thetvdb_numbering(items):
    episode = parse_episode(items[0])
    assert episode.nhk_id == "2061900"
    assert (episode.season, episode.episode) == (2026, 60)
    assert episode.numbering.basho_label == "Nagoya Basho Day 15"
    assert episode.stream_url.startswith("https://masterpl.hls.nhkworld.jp/")
    assert episode.aired == datetime(2026, 7, 26, 16, 30, tzinfo=timezone.utc)


def test_day_one_and_day_eight(items):
    assert parse_episode(items[1]).episode == 53  # Day 8
    assert parse_episode(items[2]).episode == 46  # Day 1


def test_thumbnail_prefers_the_largest_image_and_is_absolute(items):
    episode = parse_episode(items[0])
    assert episode.thumbnail.startswith("https://www3.nhk.or.jp/")
    assert "wide_l" in episode.thumbnail


def test_page_url_is_absolute(items):
    assert parse_episode(items[0]).page_url == (
        "https://www3.nhk.or.jp/nhkworld/en/shows/2061900/"
    )


def test_size_is_estimated_from_duration(items):
    episode = parse_episode(items[0])
    # 1740s of ~3.57 Mbit/s lands a little under 800 MB.
    assert 700_000_000 < episode.size_bytes < 900_000_000


def test_item_without_a_stream_is_skipped(items):
    item = dict(items[0])
    item["video"] = {"url": None}
    assert parse_episode(item) is None


def test_item_without_a_day_number_is_skipped(items):
    item = dict(items[0])
    item["title"] = "Sumopedia: The salt-throwing ritual"
    item["description"] = "A look at how rikishi purify the ring."
    assert parse_episode(item) is None


def test_expiry_is_read(items):
    episode = parse_episode(items[0])
    assert episode.expires_at == datetime(2026, 8, 10, 14, 59, tzinfo=timezone.utc)


def test_expired_flag(items):
    episode = parse_episode(items[0])
    past = Episode(**{**episode.__dict__, "expires_at": datetime(2000, 1, 1, tzinfo=timezone.utc)})
    assert past.expired
    assert not Episode(**{**episode.__dict__, "expires_at": None}).expired
