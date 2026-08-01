"""Release naming and the NZB envelope that carries the episode id."""

import json
from pathlib import Path

import pytest

from sumobridge.nhk import parse_episode
from sumobridge.releases import (
    build_nzb,
    episode_id_from_any,
    parse_nzb,
    release_name,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures_nhk_episodes.json").read_text())


@pytest.fixture
def episode():
    return parse_episode(FIXTURE["items"][0])


def test_release_name_is_scene_shaped(episode):
    assert release_name(episode) == (
        "GRAND.SUMO.Highlights.S2026E60.Nagoya.Basho.Day.15"
        ".720p.NHKW.WEB-DL.AAC2.0.H.264-SUMOBRIDGE"
    )


def test_release_name_has_no_characters_that_confuse_the_parser(episode):
    name = release_name(episode)
    assert not set("[]()") & set(name)
    assert " " not in name


def test_release_group_is_configurable(episode):
    assert release_name(episode, "MYGROUP").endswith("-MYGROUP")


def test_nzb_roundtrips_the_episode_id(episode):
    nzb = build_nzb(episode, release_name(episode))
    assert parse_nzb(nzb) == "2061900"


def test_nzb_is_well_formed_xml(episode):
    from xml.etree import ElementTree

    root = ElementTree.fromstring(build_nzb(episode, release_name(episode)))
    assert root.tag.endswith("nzb")


def test_parse_nzb_falls_back_to_the_segment_id(episode):
    nzb = build_nzb(episode, release_name(episode)).decode()
    stripped = nzb.replace('<meta type="x-nhk-episode-id">2061900</meta>', "")
    assert parse_nzb(stripped.encode()) == "2061900"


def test_parse_nzb_rejects_junk():
    assert parse_nzb(b"not xml at all") is None
    assert parse_nzb(b"<nzb><head></head></nzb>") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("http://bridge:8787/download/2061900.nzb?apikey=abc", "2061900"),
        ("/download/2061900.nzb", "2061900"),
        ("2061900", "2061900"),
        ("https://example.com/other.nzb", None),
        (None, None),
    ],
)
def test_episode_id_from_any(value, expected):
    assert episode_id_from_any(value) == expected
