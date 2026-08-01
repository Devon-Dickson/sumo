"""Newznab XML generation.

Sonarr talks to this half as if it were an ordinary usenet indexer. Newznab
rather than Torznab is deliberate: the download-client half emulates SABnzbd, so
Sonarr must classify these releases as usenet in order to pair the two.
"""

from __future__ import annotations

from email.utils import format_datetime
from xml.etree import ElementTree

from .config import TVDB_SERIES_ID, Config
from .nhk import Episode
from .releases import release_name

NEWZNAB_NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"
ATOM_NS = "http://www.w3.org/2005/Atom"

#: 5000 = TV, 5040 = TV/HD. Sonarr's default TV categories include both.
CATEGORY_TV = "5000"
CATEGORY_TV_HD = "5040"


def caps_xml() -> bytes:
    """Capabilities document Sonarr fetches when the indexer is tested."""
    caps = ElementTree.Element("caps")
    ElementTree.SubElement(
        caps, "server", {"title": "NHK Grand Sumo Bridge", "version": "1.0"}
    )
    ElementTree.SubElement(caps, "limits", {"max": "100", "default": "100"})

    searching = ElementTree.SubElement(caps, "searching")
    ElementTree.SubElement(
        searching, "search", {"available": "yes", "supportedParams": "q"}
    )
    ElementTree.SubElement(
        searching,
        "tv-search",
        {"available": "yes", "supportedParams": "q,season,ep,tvdbid"},
    )
    for unsupported in ("movie-search", "music-search", "audio-search", "book-search"):
        ElementTree.SubElement(
            searching, unsupported, {"available": "no", "supportedParams": "q"}
        )

    categories = ElementTree.SubElement(caps, "categories")
    tv = ElementTree.SubElement(
        categories, "category", {"id": CATEGORY_TV, "name": "TV"}
    )
    ElementTree.SubElement(tv, "subcat", {"id": CATEGORY_TV_HD, "name": "TV/HD"})

    return ElementTree.tostring(caps, encoding="utf-8", xml_declaration=True)


def _attr(parent: ElementTree.Element, name: str, value: str) -> None:
    ElementTree.SubElement(
        parent, f"{{{NEWZNAB_NS}}}attr", {"name": name, "value": value}
    )


def feed_xml(episodes: list[Episode], config: Config, total: int | None = None) -> bytes:
    """Render episodes as a Newznab search response."""
    ElementTree.register_namespace("newznab", NEWZNAB_NS)
    ElementTree.register_namespace("atom", ATOM_NS)

    rss = ElementTree.Element("rss", {"version": "2.0"})
    channel = ElementTree.SubElement(rss, "channel")
    ElementTree.SubElement(channel, "title").text = "NHK Grand Sumo Bridge"
    ElementTree.SubElement(channel, "description").text = (
        "GRAND SUMO Highlights episodes from NHK WORLD-JAPAN"
    )
    ElementTree.SubElement(channel, "link").text = config.public_url
    ElementTree.SubElement(
        channel,
        f"{{{NEWZNAB_NS}}}response",
        {"offset": "0", "total": str(total if total is not None else len(episodes))},
    )

    for episode in episodes:
        name = release_name(episode, config.release_group)
        download_url = (
            f"{config.public_url}/download/{episode.nhk_id}.nzb"
            f"?apikey={config.api_key}"
        )
        size = str(episode.size_bytes)

        item = ElementTree.SubElement(channel, "item")
        ElementTree.SubElement(item, "title").text = name
        ElementTree.SubElement(item, "guid", {"isPermaLink": "false"}).text = (
            f"nhk-{episode.nhk_id}"
        )
        ElementTree.SubElement(item, "link").text = download_url
        ElementTree.SubElement(item, "comments").text = episode.page_url
        ElementTree.SubElement(item, "pubDate").text = format_datetime(episode.aired)
        ElementTree.SubElement(item, "category").text = CATEGORY_TV_HD
        ElementTree.SubElement(item, "description").text = episode.description
        ElementTree.SubElement(
            item,
            "enclosure",
            {"url": download_url, "length": size, "type": "application/x-nzb"},
        )

        _attr(item, "category", CATEGORY_TV)
        _attr(item, "category", CATEGORY_TV_HD)
        _attr(item, "size", size)
        _attr(item, "tvdbid", str(TVDB_SERIES_ID))
        _attr(item, "season", str(episode.season))
        _attr(item, "episode", str(episode.episode))
        _attr(item, "grabs", "0")
        if episode.thumbnail:
            _attr(item, "coverurl", episode.thumbnail)

    return ElementTree.tostring(rss, encoding="utf-8", xml_declaration=True)


def error_xml(code: int, description: str) -> bytes:
    error = ElementTree.Element(
        "error", {"code": str(code), "description": description}
    )
    return ElementTree.tostring(error, encoding="utf-8", xml_declaration=True)
