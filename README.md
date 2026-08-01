# sumo

Code and configuration for consuming Sumo content.

Eventually this will grow into a webapp for keeping up with grand sumo
tournaments and the news between them. The first piece is **sumo-bridge**,
which gets NHK WORLD-JAPAN's *GRAND SUMO Highlights* into Sonarr and Jellyfin.

## sumo-bridge

NHK publishes a ~28 minute highlights episode for each of the fifteen days of
every tournament, free and unauthenticated, as an HLS stream. Sonarr can't
fetch an HLS stream, and there's no indexer carrying these.

sumo-bridge sits between them and speaks the two protocols Sonarr already
knows:

```
        ┌───────────────────────── sumo-bridge ─────────────────────────┐
        │                                                               │
NHK  ──▶│  Newznab indexer  ──── advertises episodes as releases        │
shows   │        ▲                                                      │
API     │        │ search / RSS                                         │
        │  ┌─────┴──────┐                                               │
        │  │   Sonarr   │                                               │
        │  └─────┬──────┘                                               │
        │        │ "download this NZB"                                  │
        │        ▼                                                      │
        │  SABnzbd client  ──── runs yt-dlp, writes an .mkv ────────────┼──▶ /downloads/complete
        │                                                               │
        └───────────────────────────────────────────────────────────────┘
                                                                             │ Sonarr imports + renames
                                                                             ▼
                                                                        Jellyfin library
```

Sonarr does the monitoring, grabbing, renaming and importing exactly as it
would for any other series, so Jellyfin needs no special handling — it just
reads the library Sonarr maintains.

### What it does

- Reads the episode list from NHK's public shows API (no key, no scraping).
- Maps each episode onto TheTVDB's numbering for
  [Grand Sumo Highlights](https://thetvdb.com/series/grand-sumo-highlights)
  (series 391618), which numbers seasons by year and episodes sequentially
  across the year's six tournaments. Day 1 of the July (Nagoya) tournament in
  2026 is `S2026E46`.
- Presents each episode as a scene-style release Sonarr's parser understands:

  ```
  GRAND.SUMO.Highlights.S2026E46.Nagoya.Basho.Day.1.720p.NHKW.WEB-DL.AAC2.0.H.264-SUMOBRIDGE
  ```

- Downloads with yt-dlp on demand, reporting progress through the SABnzbd
  queue so Sonarr's Activity tab behaves normally.

### Setup

- [docs/SETUP.md](docs/SETUP.md) — Docker Compose, and the Sonarr/Jellyfin
  screens in detail.
- [docs/PROXMOX.md](docs/PROXMOX.md) — running it on Proxmox, either inside an
  existing LXC or in one of its own, alongside a real SABnzbd.

The short version:

```bash
cp .env.example .env
# set SUMO_API_KEY to something random: openssl rand -hex 16
docker compose up -d --build
curl "http://localhost:8787/health"
```

Then in Sonarr add a **Newznab** indexer at `http://sumo-bridge:8787` and a
**SABnzbd** download client at `sumo-bridge:8787` with URL base `sabnzbd`, both
using the same API key.

### Things worth knowing

- **720p is the ceiling.** NHK's best rendition is 1280x720 H.264. A quality
  profile that demands 1080p will never grab anything.
- **Episodes expire.** NHK drops each episode about two weeks after it airs, so
  there is roughly a two-week window to catch up. Only the current tournament's
  episodes are ever available; there is no back catalogue to fetch.
- **ffmpeg is required.** Video and audio are separate HLS renditions and get
  merged on download. The Docker image includes it.
- This talks to NHK's public website API the same way a browser does, for
  personal use. It doesn't circumvent any access control, and it won't reach
  anything NHK doesn't already serve for free.

### Keeping it running

It is meant to be left alone between tournaments — Sonarr's RSS sync picks up
each day's episode on its own. Four things are worth knowing.

**Update yt-dlp occasionally.** This is the most likely thing to break over a
long gap: NHK changes its delivery, yt-dlp adapts, and a pinned copy from six
months ago doesn't. A minute's work before each basho:

```bash
/opt/sumo-bridge/venv/bin/pip install --upgrade yt-dlp
systemctl restart sumo-bridge
```

**A cancelled tournament breaks the numbering.** Episode numbers are derived
arithmetically — six tournaments a year, fifteen days each. When a basho is
cancelled TheTVDB closes the gap instead of leaving it: in 2020 the cancelled
May tournament put Nagoya Day 1 at `S2020E31`, not `S2020E46`, and every later
tournament that year shifted down by 15. If that happens again, correct it
without touching code:

```ini
SUMO_SEASON_OFFSETS=2027:-15      # one lost basho; -30 for two
```

Compare `/health`'s `latest` against TheTVDB at the start of a basho and you'll
catch it on day one rather than after fifteen misfiled episodes.

**There is no back catalogue.** NHK expires episodes about two weeks after they
air. If Sonarr is down for a fortnight mid-tournament, those episodes are gone
for good — nothing can re-fetch them.

**Budget the disk.** Roughly 700 MB an episode, so ~10 GB per tournament, ~60 GB
a year. Check that Sonarr is set to remove completed downloads, or the finished
folders pile up alongside the imported library copies.

Upgrading the bridge itself:

```bash
cd /opt/sumo-bridge/src && git pull && ./deploy/install.sh
```

### Development

```bash
pip install -e ".[dev]"
pytest
```

The tests run against a fixture captured from the live API and stub out yt-dlp,
so they need no network and no ffmpeg.
