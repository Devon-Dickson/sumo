"""Map an NHK episode onto TheTVDB's season/episode numbering.

TheTVDB (series 391618, "Grand Sumo Highlights") numbers this show as:

* **season** = the calendar year the tournament was held in;
* **episode** = a running count across the year's six tournaments, fifteen
  episodes each. So Tournament *t*, Day *d* is ``(t - 1) * 15 + d``.

Verified against two independent seasons: 2021 Tournament 2 Day 4 is S2021E19,
and 2026 Tournament 4 Day 1 is S2026E46.

Deriving the number arithmetically rather than scraping TheTVDB keeps this
offline and stable, and sidesteps the occasional wrong air date in TheTVDB's
data (2026 lists both Day 14 and Day 15 as July 25).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

DAYS_PER_TOURNAMENT = 15
TOURNAMENTS_PER_YEAR = 6


@dataclass(frozen=True)
class Tournament:
    index: int  # 1-6, position within the year
    month: int
    name: str  # traditional name, e.g. "Hatsu"
    city: str


#: The six honbasho, in the order they are held each year.
TOURNAMENTS: tuple[Tournament, ...] = (
    Tournament(1, 1, "Hatsu", "Tokyo"),
    Tournament(2, 3, "Haru", "Osaka"),
    Tournament(3, 5, "Natsu", "Tokyo"),
    Tournament(4, 7, "Nagoya", "Nagoya"),
    Tournament(5, 9, "Aki", "Tokyo"),
    Tournament(6, 11, "Kyushu", "Fukuoka"),
)

_BY_MONTH = {t.month: t for t in TOURNAMENTS}

_MONTH_NAMES = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

# NHK writes titles as "July Tournament Day 12"; TheTVDB and older NHK copy use
# the traditional name ("Nagoya Basho"). Both are accepted. Host cities are
# deliberately *not* aliases: Tokyo hosts three of the six tournaments, so it
# cannot identify one.
_ALIASES: dict[str, Tournament] = {t.name.lower(): t for t in TOURNAMENTS}
for _name, _month in _MONTH_NAMES.items():
    if _month in _BY_MONTH:
        _ALIASES[_name] = _BY_MONTH[_month]

_ALTERNATION = "|".join(sorted(_ALIASES, key=len, reverse=True))

_DAY_RE = re.compile(r"\bday\s+(\d{1,2})\b", re.IGNORECASE)

# Ordered from most to least specific. "May" is also an ordinary English word,
# so a bare alias is only trusted once the qualified forms have been ruled out.
_TOURNAMENT_RES = (
    # "July Tournament", "Nagoya Basho"
    re.compile(rf"\b({_ALTERNATION})\s+(?:tournament|basho)\b", re.IGNORECASE),
    # "...bouts from July 26, Day 15..."
    re.compile(rf"\b({_ALTERNATION})\s+\d{{1,2}}\b", re.IGNORECASE),
    re.compile(rf"\b({_ALTERNATION})\b", re.IGNORECASE),
)


class NumberingError(ValueError):
    """Raised when an episode cannot be placed in the TheTVDB numbering."""


def parse_day(*texts: str | None) -> int | None:
    """Pull the tournament day (1-15) out of an NHK title or description."""
    for text in texts:
        if not text:
            continue
        match = _DAY_RE.search(text)
        if match:
            day = int(match.group(1))
            if 1 <= day <= DAYS_PER_TOURNAMENT:
                return day
    return None


def parse_tournament(*texts: str | None) -> Tournament | None:
    """Identify the tournament from an NHK title or description.

    Tried most-specific pattern first across all inputs, so a qualified match in
    the description beats a bare-word match in the title.
    """
    for pattern in _TOURNAMENT_RES:
        for text in texts:
            if not text:
                continue
            match = pattern.search(text)
            if match:
                return _ALIASES[match.group(1).lower()]
    return None


def tournament_for_date(aired: datetime) -> Tournament:
    """Fallback: the tournament whose month is closest at or before ``aired``.

    Episodes air during their own tournament, so the airing month normally *is*
    the tournament month. Off-months only show up for stragglers published a few
    days late, which belong to the tournament that just finished.
    """
    month = aired.month
    candidates = [t for t in TOURNAMENTS if t.month <= month]
    return candidates[-1] if candidates else TOURNAMENTS[-1]


def episode_number(tournament: Tournament, day: int) -> int:
    if not 1 <= day <= DAYS_PER_TOURNAMENT:
        raise NumberingError(f"day {day} is outside 1-{DAYS_PER_TOURNAMENT}")
    return (tournament.index - 1) * DAYS_PER_TOURNAMENT + day


@dataclass(frozen=True)
class Numbering:
    season: int
    episode: int
    tournament: Tournament
    day: int

    @property
    def basho_label(self) -> str:
        """Human label used in the release name, e.g. ``Nagoya Basho Day 15``."""
        return f"{self.tournament.name} Basho Day {self.day}"


def resolve(title: str | None, description: str | None, aired: datetime) -> Numbering:
    """Work out the TheTVDB season/episode for one NHK episode.

    ``aired`` is NHK's ``first_broadcasted_at``, which is the same UTC day as
    the bouts it covers (the 16:30Z slot is 01:30 JST the following day).

    Raises :class:`NumberingError` when the day number cannot be determined —
    better to skip an episode than to advertise it under the wrong number.
    """
    day = parse_day(title, description)
    if day is None:
        raise NumberingError(
            f"no tournament day found in title {title!r} or description"
        )

    tournament = parse_tournament(title, description) or tournament_for_date(aired)
    return Numbering(
        season=aired.year,
        episode=episode_number(tournament, day),
        tournament=tournament,
        day=day,
    )
