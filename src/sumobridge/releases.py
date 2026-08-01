"""Release naming, and the minimal NZB documents that carry an episode id.

Sonarr's pipeline is: grab a Newznab item -> download the ``.nzb`` it points at
-> hand those bytes to the download client. There is no usenet here, so the
"NZB" is just an envelope: a well-formed NZB document whose only job is to carry
the NHK episode id from the indexer half of this service to the SABnzbd half.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

from .config import SERIES_TITLE
from .nhk import Episode

NZB_NS = "http://www.newzbin.com/DTD/2003/nzb"
#: Meta key holding the NHK episode id inside the generated NZB.
META_EPISODE_ID = "x-nhk-episode-id"

_UNSAFE = re.compile(r"[^A-Za-z0-9]+")


def _dotted(text: str) -> str:
    """Collapse a phrase into scene-style dot-separated tokens."""
    return _UNSAFE.sub(".", text).strip(".")


def release_name(episode: Episode, release_group: str = "SUMOBRIDGE") -> str:
    """Build the release title Sonarr will parse.

    Shaped like a conventional scene name so Sonarr's existing parser resolves
    the series, the season/episode, and the quality without custom rules::

        GRAND.SUMO.Highlights.S2026E60.Nagoya.Basho.Day.15.720p.NHKW.WEB-DL.AAC2.0.H.264-SUMOBRIDGE

    NHK's own titles are used only for the descriptive middle section; the parts
    Sonarr actually keys on come from the TheTVDB numbering.
    """
    parts = [
        _dotted(SERIES_TITLE),
        f"S{episode.season:04d}E{episode.episode:02d}",
        _dotted(episode.numbering.basho_label),
        "720p.NHKW.WEB-DL.AAC2.0.H.264",
    ]
    return f"{'.'.join(p for p in parts if p)}-{_dotted(release_group)}"


def build_nzb(episode: Episode, name: str) -> bytes:
    """Generate the envelope NZB Sonarr will hand to the download client."""
    nzb = ElementTree.Element("nzb", {"xmlns": NZB_NS})
    head = ElementTree.SubElement(nzb, "head")
    for key, value in (
        ("title", name),
        (META_EPISODE_ID, episode.nhk_id),
    ):
        meta = ElementTree.SubElement(head, "meta", {"type": key})
        meta.text = value

    file_el = ElementTree.SubElement(
        nzb,
        "file",
        {
            "poster": "sumo-bridge",
            "date": str(int(episode.aired.timestamp())),
            "subject": f'"{name}.mkv" yEnc (1/1)',
        },
    )
    groups = ElementTree.SubElement(file_el, "groups")
    ElementTree.SubElement(groups, "group").text = "alt.binaries.nhkworld"
    segments = ElementTree.SubElement(file_el, "segments")
    segment = ElementTree.SubElement(
        segments, "segment", {"bytes": str(episode.size_bytes or 1), "number": "1"}
    )
    segment.text = f"nhk-{episode.nhk_id}@sumo-bridge"

    body = ElementTree.tostring(nzb, encoding="utf-8", xml_declaration=True)
    return body


def parse_nzb(data: bytes) -> str | None:
    """Recover the NHK episode id from an NZB produced by :func:`build_nzb`."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return None

    for meta in root.iter():
        if meta.tag.rsplit("}", 1)[-1] != "meta":
            continue
        if meta.get("type") == META_EPISODE_ID and meta.text:
            return meta.text.strip()

    # Fall back to the segment id, in case a client rewrote the head.
    for segment in root.iter():
        if segment.tag.rsplit("}", 1)[-1] != "segment":
            continue
        match = re.fullmatch(r"nhk-(\w+)@sumo-bridge", (segment.text or "").strip())
        if match:
            return match.group(1)
    return None


def episode_id_from_any(value: str | None) -> str | None:
    """Extract an episode id from a download URL or a bare id.

    Covers the ``mode=addurl`` path, where Sonarr passes the enclosure URL
    straight through instead of fetching it first.
    """
    if not value:
        return None
    match = re.search(r"/download/(\w+)\.nzb", value)
    if match:
        return match.group(1)
    if re.fullmatch(r"\d{4,10}", value.strip()):
        return value.strip()
    return None
