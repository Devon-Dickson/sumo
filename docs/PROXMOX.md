# sumo-bridge on Proxmox, one service per LXC

For a setup where Jellyfin, SABnzbd and Sonarr each live in their own LXC and
pass files through a shared volume. sumo-bridge becomes a fourth LXC on that
same volume.

```
                    ┌──────────────┐
                    │ sonarr  LXC  │
                    └──┬────────┬──┘
         grabs sumo    │        │   grabs everything else
         releases      │        │
              ┌────────▼──┐  ┌──▼──────────┐
              │ sumo-     │  │ sabnzbd LXC │
              │ bridge LXC│  │  (yours)    │
              └────────┬──┘  └──┬──────────┘
                       │        │
                    ┌──▼────────▼──┐
                    │ shared volume│  /downloads
                    └──────┬───────┘
                           │ Sonarr imports + renames
                    ┌──────▼───────┐
                    │ media volume │  ──▶ jellyfin LXC
                    └──────────────┘
```

## The one thing to get right

sumo-bridge *impersonates* SABnzbd, and you already run the real thing. Sonarr
is happy to hold two SABnzbd clients at once, but you have to make sure sumo
releases go to the bridge and nothing else does. Two settings do that:

- a **category** the real SABnzbd doesn't have (`sumo`), and
- **pinning the NHK indexer to the bridge client** in Sonarr.

Skip both and Sonarr may hand an NHK grab to your real SABnzbd, which will
reject it as a malformed NZB.

## Where to run it

**Inside the Sonarr LXC (option A) is the easier path, and the recommended
one.** Sonarr's container already mounts the shared volume at the path Sonarr
expects, so the bridge inherits correct paths for free: no new mount, no UID
mapping to match, no Remote Path Mapping, and Sonarr reaches it on
`127.0.0.1`. Running as Sonarr's own user also makes the post-import cleanup
permissions a non-issue.

The cost is ffmpeg and a few minutes of mux CPU landing in the Sonarr LXC once
a day during a tournament. On any hardware running Jellyfin this is noise.

Take option B — a dedicated LXC — if you'd rather keep one service per
container, or want to cap the bridge's resources separately.

Either way, steps 4 onward are identical.

---

# Option A: inside the existing Sonarr LXC

```bash
pct enter <sonarr-vmid>
```

Find the user Sonarr runs as and the path it sees downloads at:

```bash
systemctl show -p User --value sonarr     # often 'sonarr'
ls -d /downloads/complete                 # or wherever your volume is mounted
```

Install, reusing Sonarr's own user and group so everything it creates is
already owned correctly:

```bash
apt-get update && apt-get install -y git
git clone https://github.com/Devon-Dickson/sumo.git /tmp/sumo

SERVICE_USER=sonarr MEDIA_GROUP=$(id -gn sonarr) \
  /tmp/sumo/deploy/install.sh
```

The script skips creating a user that already exists, so this just builds the
venv, installs ffmpeg, and drops in the systemd unit.

Edit `/etc/sumo-bridge/sumo-bridge.env`:

```ini
SUMO_API_KEY=<the generated key>
SUMO_PUBLIC_URL=http://127.0.0.1:8787
SUMO_COMPLETE_DIR=/downloads/complete
SUMO_INCOMPLETE_DIR=/downloads/incomplete
SUMO_CATEGORY=sumo
```

`SUMO_PUBLIC_URL` can be loopback here precisely because Sonarr is the only
thing that fetches those links, and it's in the same container.

The installer has already written `User=`/`Group=` into the unit from the
variables you passed. Check `ReadWritePaths=` matches your real mount point,
then start it:

```bash
grep -E '^(User|Group|ReadWritePaths)=' /etc/systemd/system/sumo-bridge.service
systemctl start sumo-bridge
curl http://127.0.0.1:8787/health
```

Then skip to **step 4**. In steps 5 and 6, use `127.0.0.1` as the URL and Host,
and ignore the Remote Path Mapping section entirely — the paths already match.

> Prefer the Docker image inside an existing LXC? That works too, but the LXC
> needs `--features nesting=1,keyctl=1` set from the Proxmox host first
> (`pct set <vmid> --features nesting=1,keyctl=1` then restart). Mount
> `/downloads` through to the container in `docker-compose.yml` at the same
> path the host LXC sees, and set `SUMO_PUBLIC_URL` to the LXC's IP rather
> than loopback, since the container has its own network namespace.

---

# Option B: a dedicated LXC

## 1. Find out what your existing LXCs do

Run on the Proxmox host. The new LXC has to match, so don't guess.

```bash
# Substitute your SABnzbd and Sonarr container IDs.
pct config 101 | grep -E '^(mp[0-9]|rootfs|unprivileged)'
pct config 102 | grep -E '^(mp[0-9]|rootfs|unprivileged)'
```

You're after two things:

- the **host path and mount point**, e.g. `mp0: /srv/media,mp=/downloads`
- whether the containers are **unprivileged** (`unprivileged: 1`)

Then find the group that owns the shared files inside SABnzbd's container:

```bash
pct exec 101 -- id sabnzbd
pct exec 101 -- stat -c '%U %G %u %g %a' /downloads/complete
```

Note the **GID**. Everything below assumes `13000`; use whatever you actually
see.

## 2. Create the LXC

```bash
pveam update
pveam download local debian-12-standard_12.7-1_amd64.tar.zst

pct create 150 local:vztmpl/debian-12-standard_12.7-1_amd64.tar.zst \
  --hostname sumo-bridge \
  --cores 2 --memory 1024 --swap 512 \
  --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 \
  --onboot 1 \
  --start 1
```

2 cores and 1 GB is plenty — the work is network I/O plus a short ffmpeg mux.
8 GB of root disk leaves room for an episode in flight; the episodes themselves
land on the shared volume, not here.

> Nesting (`--features nesting=1`) is only needed if you'd rather run the
> Docker image inside the LXC. For the systemd install below, leave it off.

Attach the shared volume at **the same mount point the other containers use**:

```bash
pct set 150 -mp0 /srv/media,mp=/downloads
```

### Permissions, if the containers are unprivileged

An unprivileged LXC shifts IDs by 100000: container GID 13000 is host GID
113000. Containers that use the *same* in-container GID therefore land on the
same host GID, and the shared volume works between them.

Check what the host actually has:

```bash
ls -ln /srv/media/downloads     # numeric owner/group
```

If the group column reads `113000`, matching GID 13000 inside the new container
is all you need. Make the shared directories group-writable and setgid, so new
files inherit the group instead of the creator's:

```bash
chmod -R 2775 /srv/media/downloads
```

The `UMask=0002` in the systemd unit is the other half of this — without it the
bridge writes files Sonarr can read but not delete after importing.

## 3. Install the bridge

Inside the container (`pct enter 150`):

```bash
apt-get update && apt-get install -y git
git clone https://github.com/Devon-Dickson/sumo.git /tmp/sumo
MEDIA_GID=13000 /tmp/sumo/deploy/install.sh
```

That installs Python and **ffmpeg** (required — NHK serves video and audio as
separate renditions), builds a venv under `/opt/sumo-bridge`, creates a
`sumobridge` service user in the `media` group, and writes a systemd unit. It
prints a generated API key; keep it.

Edit `/etc/sumo-bridge/sumo-bridge.env`:

```ini
SUMO_API_KEY=<the generated key>
SUMO_PUBLIC_URL=http://192.168.1.50:8787   # this LXC's IP, as Sonarr sees it
SUMO_COMPLETE_DIR=/downloads/complete
SUMO_INCOMPLETE_DIR=/downloads/incomplete
SUMO_CATEGORY=sumo
```

`SUMO_PUBLIC_URL` is not cosmetic: the `.nzb` links inside the feed are built
from it, and Sonarr fetches them. `localhost` will not work.

If your volume is mounted somewhere other than `/downloads`, update
`ReadWritePaths=` in `/etc/systemd/system/sumo-bridge.service` too — the unit
runs with `ProtectSystem=strict`, so an unlisted path is read-only.

```bash
systemctl start sumo-bridge
curl http://127.0.0.1:8787/health
```

Confirm it can actually write where it claims:

```bash
sudo -u sumobridge touch /downloads/complete/.probe && \
  echo "writable" && rm /downloads/complete/.probe
```

---

# Both options continue here

In steps 5 and 6 below, substitute `127.0.0.1` for `192.168.1.50` if you took
option A.

## 4. Sonarr: add the series

**Series → Add New → "Grand Sumo Highlights"** (TheTVDB 391618).

Use a quality profile that accepts **WEBDL-720p**. NHK's best rendition is
1280x720, so a 1080p-only profile silently rejects every release. This is the
most common failure.

Series Type: **Standard**.

## 5. Sonarr: add the indexer

**Settings → Indexers → Add → Newznab** (generic, not a preset).

| Setting | Value |
| --- | --- |
| Name | `NHK Grand Sumo` |
| URL | `http://192.168.1.50:8787` |
| API Path | `/api` |
| API Key | your `SUMO_API_KEY` |
| Categories | `5000`, `5040` |

## 6. Sonarr: add the second download client

**Settings → Download Clients → Add → SABnzbd.**

| Setting | Value |
| --- | --- |
| Name | `sumo-bridge` |
| Host | `192.168.1.50` |
| Port | `8787` |
| **URL Base** | `sabnzbd` |
| API Key | your `SUMO_API_KEY` |
| Category | `sumo` |
| Use SSL | off |

`URL Base` is mandatory — it's what stops the download-client API colliding
with the indexer API on the same port.

Do **not** create a `sumo` category in your real SABnzbd. Its absence there is
part of what keeps the two apart.

### Pin the indexer to this client

**Settings → Indexers → NHK Grand Sumo → Show Advanced → Download Client →
`sumo-bridge`.**

This is the setting that guarantees sumo grabs never reach your real SABnzbd.
If your Sonarr version doesn't offer a per-indexer download client, fall back to
**Client Priority**: leave the real SABnzbd at a lower priority number and rely
on the distinct category, then confirm the first grab landed correctly.

### Path mapping

The bridge reports finished downloads at
`/downloads/complete/sumo/<release name>/`.

If Sonarr's LXC mounts the same host directory at the same path, you're done.
If Sonarr sees it as, say, `/data/downloads`, add
**Settings → Download Clients → Remote Path Mapping**:

| Field | Value |
| --- | --- |
| Host | `192.168.1.50` (exactly the client's Host field) |
| Remote Path | `/downloads/complete/` |
| Local Path | `/data/downloads/complete/` |

> Worth checking while you're here: if your downloads and media volumes are
> separate mounts, Sonarr copies rather than hardlinks on import, so an episode
> briefly exists twice. That's your existing SABnzbd behaviour too, not
> something the bridge changes.

## 7. Jellyfin

Nothing sumo-specific. Sonarr has already renamed the files into the layout
Jellyfin expects, and Jellyfin reads the *media* volume, not `/downloads`.

If the show isn't showing up:

1. Confirm the TV library covers Sonarr's root folder.
2. Metadata downloader **TheTVDB** on — it matches the `S2026E46` pattern and
   fills in titles, air dates and thumbnails.
3. **Sonarr → Settings → Connect → Add → Emby/Jellyfin** triggers a targeted
   rescan on import, instead of waiting for a scheduled one.

## 8. Verify end to end

With a tournament in progress or recently finished:

```bash
# 1. Bridge sees NHK
curl -s http://192.168.1.50:8787/health

# 2. Feed renders (from the Sonarr LXC, to prove it can reach the bridge)
curl -s "http://192.168.1.50:8787/api?t=tvsearch&apikey=$KEY" | head -20
```

Then in Sonarr: **Series → Grand Sumo Highlights → Season 2026 → Search.**

Watch **Activity → Queue** — the release should show a climbing percentage,
then vanish into History as Sonarr imports it. A 28-minute episode takes a
couple of minutes.

```bash
# What the bridge thinks it did
curl -s "http://192.168.1.50:8787/sabnzbd/api?mode=history&output=json&apikey=$KEY"
journalctl -u sumo-bridge -f
```

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| Indexer test fails | Sonarr can't reach the LXC, or key mismatch. Test from inside Sonarr's LXC: `curl http://192.168.1.50:8787/health`. |
| Grab appears in your **real** SABnzbd and fails | The indexer isn't pinned to the bridge client, or the category isn't `sumo`. See step 6. |
| Releases listed but never grabbed | Quality profile doesn't allow WEBDL-720p. |
| Service won't start, "Read-only file system" | `ReadWritePaths=` in the unit doesn't match your mount point. |
| Download completes, Sonarr won't import | Path mismatch — compare `storage` in the history JSON against what Sonarr sees. Add a Remote Path Mapping. |
| Sonarr imports but can't delete the leftover folder | Permissions. Check `UMask=0002`, the setgid bit (`chmod 2775`), and that both containers share the GID. |
| `episodes: 0` from `/health` | Normal between tournaments — NHK's feed empties once the previous basho expires. |
| "no longer offered by NHK" | The episode expired before Sonarr grabbed it. NHK keeps roughly a two-week window. |

Upgrading later: re-run `deploy/install.sh`. It pulls, rebuilds the venv, and
leaves your env file alone.
