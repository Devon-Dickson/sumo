# Setting up sumo-bridge with Sonarr and Jellyfin

Order matters a little: start the bridge, wire up Sonarr, then point Jellyfin at
whatever Sonarr produces.

## 1. Start the bridge

```bash
cp .env.example .env
```

Set `SUMO_API_KEY` to something random (`openssl rand -hex 16`). If you leave it
unset the service invents one at startup and it changes on every restart, which
breaks Sonarr's saved settings.

Set `DOWNLOADS_DIR` to the directory Sonarr already uses for downloads, so both
containers see the same files at the same paths.

```bash
docker compose up -d --build
curl http://localhost:8787/health
```

A healthy response looks like:

```json
{
  "status": "ok",
  "episodes": 15,
  "queued": 0,
  "history": 0,
  "latest": {
    "release": "GRAND.SUMO.Highlights.S2026E60.Nagoya.Basho.Day.15.720p.NHKW.WEB-DL.AAC2.0.H.264-SUMOBRIDGE",
    "season": 2026,
    "episode": 60
  }
}
```

`episodes: 0` between tournaments is normal — NHK's feed empties out once the
previous basho's episodes expire.

## 2. Add the series to Sonarr

**Series → Add New → search "Grand Sumo Highlights"** (TheTVDB 391618).

| Setting | Value |
| --- | --- |
| Monitor | *Future Episodes*, or *All Episodes* to grab the current tournament |
| Quality Profile | one that accepts **WEBDL-720p** |
| Series Type | **Standard** |
| Season Folder | your preference |

> The quality profile is the most common thing to get wrong. NHK tops out at
> 720p, so a profile requiring 1080p will show releases as rejected.

## 3. Add the indexer

**Settings → Indexers → Add → Newznab** (the custom/generic one, not a preset).

| Setting | Value |
| --- | --- |
| Name | `NHK Grand Sumo` |
| URL | `http://sumo-bridge:8787` |
| API Path | `/api` |
| API Key | your `SUMO_API_KEY` |
| Categories | `5000`, `5040` |

Use `http://sumo-bridge:8787` when Sonarr is in the same compose project. If
it's elsewhere, use the host's address and make sure `SUMO_PUBLIC_URL` matches
what Sonarr can reach — the download links in the feed are built from it.

**Test** should pass. If it doesn't, check `docker compose logs sumo-bridge`.

## 4. Add the download client

**Settings → Download Clients → Add → SABnzbd.**

| Setting | Value |
| --- | --- |
| Name | `sumo-bridge` |
| Host | `sumo-bridge` |
| Port | `8787` |
| URL Base | `sabnzbd` |
| API Key | your `SUMO_API_KEY` |
| Category | `sumo` |
| Use SSL | off |

The `URL Base` of `sabnzbd` is what keeps the download-client API from colliding
with the indexer API on the same port. It is not optional.

Under **Settings → Download Clients → Completed Download Handling**, leave
*Enable* on. That's what makes Sonarr import from the finished folder.

### Paths

Sonarr imports from the `storage` path the bridge reports, which is
`<SUMO_COMPLETE_DIR>/<category>/<release name>/` — by default
`/downloads/complete/sumo/…`.

If Sonarr sees that directory at a different path than the bridge does, add
**Settings → Download Clients → Remote Path Mapping**:

| Field | Value |
| --- | --- |
| Host | `sumo-bridge` |
| Remote Path | `/downloads/complete/` |
| Local Path | wherever Sonarr has it mounted |

Mounting the same directory at the same path in both containers avoids this
entirely, and is what `docker-compose.yml` does.

## 5. Grab something

With a tournament in progress (or within about two weeks of one ending):

**Series → Grand Sumo Highlights → Season 2026 → Search.**

You should see releases appear, move through Activity → Queue with a progress
percentage, then land in the library. First run takes a couple of minutes per
episode.

To check by hand instead:

```bash
curl "http://localhost:8787/api?t=tvsearch&apikey=$SUMO_API_KEY" | head -40
```

## 6. Jellyfin

Nothing sumo-specific here — Sonarr has already named the files the way Jellyfin
expects.

1. **Dashboard → Libraries → Add Media Library**, type **Shows**.
2. Point it at Sonarr's TV root folder (the same one the series uses).
3. Metadata downloaders: **TheTVDB** on. It matches on the folder name and the
   `S2026E46` pattern, so episodes get the right titles, air dates and
   thumbnails.
4. **Scan All Libraries**, or let Sonarr notify Jellyfin:
   **Sonarr → Settings → Connect → Add → Emby/Jellyfin**, which triggers a
   targeted rescan on import.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Indexer test fails | `SUMO_API_KEY` mismatch, or Sonarr can't resolve the host. Check `curl http://sumo-bridge:8787/health` from inside the Sonarr container. |
| Releases found but never grabbed | Quality profile doesn't allow WEBDL-720p. |
| Download completes, Sonarr doesn't import | Path mismatch. Compare `storage` in `…/sabnzbd/api?mode=history&apikey=…` against what Sonarr can see. |
| `episodes: 0` from `/health` | Between tournaments, or NHK changed the feed. Check the logs. |
| Download fails with "no longer offered by NHK" | The episode expired before Sonarr got to it. |
| Downloads fail with an ffmpeg error | Running outside Docker without ffmpeg installed. |

Useful endpoints:

```bash
curl "http://localhost:8787/health"
curl "http://localhost:8787/api?t=caps"
curl "http://localhost:8787/sabnzbd/api?mode=queue&output=json&apikey=$SUMO_API_KEY"
curl "http://localhost:8787/sabnzbd/api?mode=history&output=json&apikey=$SUMO_API_KEY"
```

## Configuration reference

| Variable | Default | Meaning |
| --- | --- | --- |
| `SUMO_API_KEY` | generated | Shared secret for both APIs. Set it. |
| `SUMO_PUBLIC_URL` | `http://localhost:8787` | Base URL for the `.nzb` links, as Sonarr resolves it. |
| `SUMO_HOST` / `SUMO_PORT` | `0.0.0.0` / `8787` | Listen address. |
| `SUMO_CATEGORY` | `sumo` | SABnzbd category; also the subfolder under the completed dir. |
| `SUMO_COMPLETE_DIR` | `/downloads/complete` | Where finished downloads land. |
| `SUMO_INCOMPLETE_DIR` | `/downloads/incomplete` | Work area. Keep on the same filesystem as the completed dir. |
| `SUMO_STATE_FILE` | `/config/state.json` | Queue and history, so restarts don't orphan grabs. |
| `SUMO_MAX_CONCURRENT` | `1` | Parallel downloads. |
| `SUMO_VIDEO_FORMAT` | `bestvideo[height<=?720]+bestaudio/best` | yt-dlp format selector. |
| `SUMO_RELEASE_GROUP` | `SUMOBRIDGE` | Release-name suffix. |
| `SUMO_CACHE_TTL` | `300` | Seconds to cache NHK's listing. |
| `SUMO_LANG` | `en` | NHK feed language. |
