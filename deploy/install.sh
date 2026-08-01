#!/usr/bin/env bash
#
# Install sumo-bridge as a systemd service on a Debian/Ubuntu LXC.
#
#   curl -fsSL .../install.sh | sudo bash        # or, from a clone:
#   sudo ./deploy/install.sh
#
# Idempotent: safe to re-run to upgrade an existing install.

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Devon-Dickson/sumo.git}"
PREFIX="${PREFIX:-/opt/sumo-bridge}"
CONFIG_DIR="${CONFIG_DIR:-/etc/sumo-bridge}"
SERVICE_USER="${SERVICE_USER:-sumobridge}"
# Group that owns the shared download volume. Must match the GID the other
# LXCs use, or the finished files will not be readable by Sonarr.
MEDIA_GROUP="${MEDIA_GROUP:-media}"
MEDIA_GID="${MEDIA_GID:-13000}"

log() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root"

log "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
# ffmpeg is required: NHK serves video and audio as separate HLS renditions.
apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip ffmpeg git ca-certificates

log "Ensuring group ${MEDIA_GROUP} (gid ${MEDIA_GID})"
if ! getent group "$MEDIA_GROUP" >/dev/null; then
    groupadd -g "$MEDIA_GID" "$MEDIA_GROUP"
else
    existing_gid=$(getent group "$MEDIA_GROUP" | cut -d: -f3)
    [[ "$existing_gid" == "$MEDIA_GID" ]] || \
        log "WARNING: ${MEDIA_GROUP} already exists with gid ${existing_gid}, not ${MEDIA_GID}."
fi

log "Ensuring service user ${SERVICE_USER}"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    useradd --system --no-create-home --shell /usr/sbin/nologin \
            --gid "$MEDIA_GROUP" "$SERVICE_USER"
else
    usermod -aG "$MEDIA_GROUP" "$SERVICE_USER"
fi

log "Fetching source into ${PREFIX}"
if [[ -d "${PREFIX}/src/.git" ]]; then
    git -C "${PREFIX}/src" pull --ff-only
else
    rm -rf "${PREFIX}/src"
    mkdir -p "$PREFIX"
    git clone --depth 1 "$REPO_URL" "${PREFIX}/src"
fi

log "Building virtualenv"
python3 -m venv "${PREFIX}/venv"
"${PREFIX}/venv/bin/pip" install --quiet --upgrade pip
"${PREFIX}/venv/bin/pip" install --quiet "${PREFIX}/src"

log "Installing config"
mkdir -p "$CONFIG_DIR"
if [[ ! -f "${CONFIG_DIR}/sumo-bridge.env" ]]; then
    install -m 640 -o root -g "$SERVICE_USER" \
        "${PREFIX}/src/deploy/sumo-bridge.env.example" \
        "${CONFIG_DIR}/sumo-bridge.env"
    # Don't ship a default secret -- generate a real one.
    key=$(openssl rand -hex 16 2>/dev/null || head -c16 /dev/urandom | od -An -tx1 | tr -d ' \n')
    sed -i "s/^SUMO_API_KEY=.*/SUMO_API_KEY=${key}/" "${CONFIG_DIR}/sumo-bridge.env"
    log "Generated API key: ${key}"
else
    log "Keeping existing ${CONFIG_DIR}/sumo-bridge.env"
fi

log "Installing systemd unit (User=${SERVICE_USER}, Group=${MEDIA_GROUP})"
# Substituted rather than copied verbatim, so installing into an existing LXC
# can reuse that host's service account (e.g. SERVICE_USER=sonarr).
sed -e "s/^User=.*/User=${SERVICE_USER}/" \
    -e "s/^Group=.*/Group=${MEDIA_GROUP}/" \
    "${PREFIX}/src/deploy/sumo-bridge.service" \
    > /etc/systemd/system/sumo-bridge.service
chmod 644 /etc/systemd/system/sumo-bridge.service
systemctl daemon-reload
systemctl enable sumo-bridge.service

cat <<EOF

Installed. Before starting, edit ${CONFIG_DIR}/sumo-bridge.env and set:

  SUMO_PUBLIC_URL   this LXC's address as Sonarr sees it, e.g. http://$(hostname -I 2>/dev/null | awk '{print $1}'):8787
  SUMO_COMPLETE_DIR path on the shared volume, matching what Sonarr sees

If your shared volume is not mounted at /downloads, also update ReadWritePaths=
in /etc/systemd/system/sumo-bridge.service, then: systemctl daemon-reload

Then:
  systemctl start sumo-bridge
  curl http://127.0.0.1:8787/health

EOF
