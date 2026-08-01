"""End-to-end walk through the flow Sonarr drives.

Covers both halves against the real payload: capabilities, the search feed, the
NZB handoff, and the SABnzbd add/queue/history/delete cycle — with yt-dlp
stubbed out so no network or ffmpeg is needed.
"""

import json
import time
from pathlib import Path
from xml.etree import ElementTree

import pytest
from fastapi.testclient import TestClient

from sumobridge import ytdlp
from sumobridge.app import create_app
from sumobridge.config import Config
from sumobridge.nhk import NhkClient, parse_episode

FIXTURE = json.loads((Path(__file__).parent / "fixtures_nhk_episodes.json").read_text())
NEWZNAB_NS = "http://www.newznab.com/DTD/2010/feeds/attributes/"
API_KEY = "testkey"


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        public_url="http://bridge:8787",
        api_key=API_KEY,
        incomplete_dir=tmp_path / "incomplete",
        complete_dir=tmp_path / "complete",
        state_file=tmp_path / "state.json",
        category="sumo",
    )


@pytest.fixture
def client(config, monkeypatch):
    episodes = [parse_episode(item) for item in FIXTURE["items"]]

    async def fake_episodes(self, force: bool = False):
        return episodes

    monkeypatch.setattr(NhkClient, "episodes", fake_episodes)

    def fake_download(stream_url, work_dir, name, video_format, on_progress=None):
        target = Path(work_dir) / f"{name}.mkv"
        target.write_bytes(b"fake video payload")
        if on_progress:
            on_progress(18, 18)
        return target

    monkeypatch.setattr(ytdlp, "download", fake_download)

    with TestClient(create_app(config)) as test_client:
        yield test_client


def attrs(item, name):
    return [
        el.get("value")
        for el in item.findall(f"{{{NEWZNAB_NS}}}attr")
        if el.get("name") == name
    ]


# -- Newznab ------------------------------------------------------------------


def test_caps_is_served_without_a_key(client):
    response = client.get("/api", params={"t": "caps"})
    assert response.status_code == 200
    caps = ElementTree.fromstring(response.content)
    tv_search = caps.find("./searching/tv-search")
    assert tv_search.get("available") == "yes"
    assert "tvdbid" in tv_search.get("supportedParams")
    assert caps.find(".//subcat[@id='5040']") is not None


def test_search_requires_the_api_key(client):
    assert client.get("/api", params={"t": "tvsearch"}).status_code == 401


def test_rss_sync_returns_every_episode(client):
    response = client.get("/api", params={"t": "tvsearch", "apikey": API_KEY})
    channel = ElementTree.fromstring(response.content).find("channel")
    items = channel.findall("item")
    assert len(items) == 3
    assert channel.find(f"{{{NEWZNAB_NS}}}response").get("total") == "3"


def test_item_carries_what_sonarr_matches_on(client):
    response = client.get("/api", params={"t": "tvsearch", "apikey": API_KEY})
    item = ElementTree.fromstring(response.content).find("channel/item")

    assert item.find("title").text == (
        "GRAND.SUMO.Highlights.S2026E60.Nagoya.Basho.Day.15"
        ".720p.NHKW.WEB-DL.AAC2.0.H.264-SUMOBRIDGE"
    )
    assert attrs(item, "tvdbid") == ["391618"]
    assert attrs(item, "season") == ["2026"]
    assert attrs(item, "episode") == ["60"]
    assert attrs(item, "category") == ["5000", "5040"]

    enclosure = item.find("enclosure")
    assert enclosure.get("type") == "application/x-nzb"
    assert enclosure.get("url").startswith("http://bridge:8787/download/2061900.nzb")
    assert int(enclosure.get("length")) > 0


def test_season_and_episode_filters(client):
    response = client.get(
        "/api",
        params={"t": "tvsearch", "apikey": API_KEY, "season": "2026", "ep": "53"},
    )
    items = ElementTree.fromstring(response.content).findall("channel/item")
    assert len(items) == 1
    assert "S2026E53" in items[0].find("title").text


def test_wrong_season_returns_nothing(client):
    response = client.get(
        "/api", params={"t": "tvsearch", "apikey": API_KEY, "season": "2019"}
    )
    assert ElementTree.fromstring(response.content).findall("channel/item") == []


def test_wrong_tvdbid_returns_nothing(client):
    response = client.get(
        "/api", params={"t": "tvsearch", "apikey": API_KEY, "tvdbid": "12345"}
    )
    assert ElementTree.fromstring(response.content).findall("channel/item") == []


def test_unknown_function_is_an_error(client):
    response = client.get("/api", params={"t": "movie", "apikey": API_KEY})
    assert response.status_code == 400
    assert ElementTree.fromstring(response.content).get("code") == "202"


def test_nzb_download(client):
    response = client.get("/download/2061900.nzb", params={"apikey": API_KEY})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-nzb")
    assert "S2026E60" in response.headers["content-disposition"]
    assert b"2061900" in response.content


def test_nzb_download_requires_the_key(client):
    assert client.get("/download/2061900.nzb").status_code == 401


def test_nzb_download_of_an_unknown_episode(client):
    response = client.get("/download/9999999.nzb", params={"apikey": API_KEY})
    assert response.status_code == 404


# -- SABnzbd ------------------------------------------------------------------


def sab(client, **params):
    return client.get("/sabnzbd/api", params={"apikey": API_KEY, **params})


def await_history(client, nzo_id, tries=50):
    """Poll history the way Sonarr does, until the job leaves the queue."""
    for _ in range(tries):
        slots = sab(client, mode="history", output="json").json()["history"]["slots"]
        for slot in slots:
            if slot["nzo_id"] == nzo_id:
                return slot
        time.sleep(0.02)
    raise AssertionError(f"{nzo_id} never reached history")


def test_version_and_config(client):
    assert sab(client, mode="version").json()["version"]

    config = sab(client, mode="get_config").json()["config"]
    assert config["misc"]["complete_dir"]
    assert {c["name"] for c in config["categories"]} == {"*", "sumo"}


def test_sab_requires_the_key(client):
    assert client.get("/sabnzbd/api", params={"mode": "version"}).status_code == 401


def test_grab_to_import(client, config):
    """The whole Sonarr cycle: fetch NZB, add it, watch it land in history."""
    nzb = client.get("/download/2061900.nzb", params={"apikey": API_KEY}).content

    added = client.post(
        "/sabnzbd/api",
        params={"apikey": API_KEY, "mode": "addfile", "cat": "sumo"},
        files={"nzbfile": ("release.nzb", nzb, "application/x-nzb")},
    ).json()
    assert added["status"] is True
    nzo_id = added["nzo_ids"][0]

    entry = await_history(client, nzo_id)
    assert entry["status"] == "Completed"

    # `storage` is what Sonarr imports from, so the file must really be there.
    storage = Path(entry["storage"])
    assert storage.parent == config.complete_dir / "sumo"
    assert list(storage.glob("*.mkv"))

    queue = sab(client, mode="queue", output="json").json()["queue"]
    assert queue["slots"] == []


def test_addurl_is_accepted(client):
    added = sab(
        client,
        mode="addurl",
        name="http://bridge:8787/download/2061893.nzb?apikey=testkey",
    ).json()
    assert added["status"] is True
    assert added["nzo_ids"] == ["SABnzbd_nzo_2061893"]


def test_adding_the_same_episode_twice_reuses_the_job(client):
    first = sab(client, mode="addurl", name="2061886").json()["nzo_ids"]
    second = sab(client, mode="addurl", name="2061886").json()["nzo_ids"]
    assert first == second


def test_add_of_an_unknown_episode_fails(client):
    response = sab(client, mode="addurl", name="9999999")
    assert response.status_code == 404
    assert response.json()["status"] is False


def test_add_of_junk_fails(client):
    response = client.post(
        "/sabnzbd/api",
        params={"apikey": API_KEY, "mode": "addfile"},
        files={"nzbfile": ("x.nzb", b"garbage", "application/x-nzb")},
    )
    assert response.status_code == 400


def test_history_delete_removes_the_files(client):
    nzb = client.get("/download/2061900.nzb", params={"apikey": API_KEY}).content
    added = client.post(
        "/sabnzbd/api",
        params={"apikey": API_KEY, "mode": "addfile"},
        files={"nzbfile": ("release.nzb", nzb, "application/x-nzb")},
    ).json()
    nzo_id = added["nzo_ids"][0]
    storage = Path(await_history(client, nzo_id)["storage"])
    assert storage.exists()

    deleted = sab(
        client, mode="history", name="delete", value=nzo_id, del_files="1"
    ).json()
    assert deleted["status"] is True
    assert not storage.exists()
    assert sab(client, mode="history").json()["history"]["slots"] == []


def test_unsupported_mode(client):
    assert sab(client, mode="restart").status_code == 400


# -- diagnostics --------------------------------------------------------------


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["episodes"] == 3
    assert body["latest"]["episode"] == 60
