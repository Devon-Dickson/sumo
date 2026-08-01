"""The numbering rule is the part that silently corrupts a library if wrong."""

from datetime import datetime, timezone

import pytest

from sumobridge.numbering import (
    NumberingError,
    parse_day,
    parse_tournament,
    resolve,
    tournament_for_date,
)


def aired(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 16, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("July Tournament Day 15", 15),
        ("[Recap] July Tournament Day 15 (Final Day)", 15),
        ("[Recap] July Tournament Day 8 (Halfway Point)", 8),
        ("[Recap] May Tournament Day 1 (Opening Day)", 1),
        ("Tournament 4 - Nagoya Basho - Day 3", 3),
        ("Sumopedia: What is a yokozuna?", None),
        ("July Tournament Day 16", None),
    ],
)
def test_parse_day(title, expected):
    assert parse_day(title) == expected


def test_parse_day_falls_back_to_description():
    description = (
        "Today the show features all top-division bouts from July 26, "
        "Day 15 of the Grand Sumo Tournament in Nagoya."
    )
    assert parse_day("Highlights", description) == 15


@pytest.mark.parametrize(
    ("text", "expected_index"),
    [
        ("July Tournament Day 15", 4),
        ("January Tournament Day 2", 1),
        ("March Tournament Day 9", 2),
        ("May Tournament Day 1", 3),
        ("September Tournament Day 7", 5),
        ("November Tournament Day 11", 6),
        ("Tournament 4 - Nagoya Basho - Day 3", 4),
        ("Tournament 6 - Kyushu Basho - Day 1", 6),
    ],
)
def test_parse_tournament(text, expected_index):
    tournament = parse_tournament(text)
    assert tournament is not None
    assert tournament.index == expected_index


def test_host_city_is_not_used_as_an_alias():
    """Tokyo hosts three tournaments, so it must never identify one."""
    assert parse_tournament("bouts at the Grand Sumo Tournament in Tokyo") is None


def test_qualified_match_beats_bare_word():
    """'May' is an ordinary English word; the qualified form must win."""
    tournament = parse_tournament(
        "September Tournament Day 4",
        "Bouts may be replayed. From September 16.",
    )
    assert tournament.index == 5


def test_tournament_for_date_uses_the_most_recent_tournament():
    assert tournament_for_date(aired(2026, 7, 20)).index == 4
    # A straggler published in August still belongs to the July tournament.
    assert tournament_for_date(aired(2026, 8, 2)).index == 4
    assert tournament_for_date(aired(2026, 1, 3)).index == 1


@pytest.mark.parametrize(
    ("title", "date", "season", "episode"),
    [
        # Verified against TheTVDB series 391618.
        ("Tournament 2 - Haru Basho - Day 4", (2021, 3, 17), 2021, 19),
        ("July Tournament Day 1", (2026, 7, 12), 2026, 46),
        ("[Recap] July Tournament Day 15 (Final Day)", (2026, 7, 26), 2026, 60),
        ("January Tournament Day 1", (2026, 1, 11), 2026, 1),
        ("March Tournament Day 15", (2026, 3, 22), 2026, 30),
        ("May Tournament Day 15", (2026, 5, 24), 2026, 45),
        ("November Tournament Day 15", (2025, 11, 23), 2025, 90),
    ],
)
def test_resolve_matches_thetvdb(title, date, season, episode):
    numbering = resolve(title, "", aired(*date))
    assert (numbering.season, numbering.episode) == (season, episode)


def test_resolve_rejects_episodes_without_a_day():
    with pytest.raises(NumberingError):
        resolve("Sumopedia: The referee's fan", "A look at the gunbai.", aired(2026, 7, 20))


def test_basho_label():
    numbering = resolve("July Tournament Day 15", "", aired(2026, 7, 26))
    assert numbering.basho_label == "Nagoya Basho Day 15"
