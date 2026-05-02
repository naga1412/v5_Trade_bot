#!/usr/bin/env bash
# Nightly full base backup, then upload to B2 and rsync to laptop.
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/var/backups/trading-radar}"
LOG="${LOG:-/var/log/trading-radar/pg_basebackup_nightly.log}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="$BACKUP_DIR/full_${TIMESTAMP}"

mkdir -p "$TARGET" "$(dirname "$LOG")"
set -a
[[ -f /home/ubuntu/trading-radar/.env ]] && . /home/ubuntu/trading-radar/.env
set +a

cd /home/ubuntu/trading-radar
docker compose exec -T postgres pg_basebackup \
    -U "${POSTGRES_USER:-postgres}" \
    -D /tmp/basebackup \
    -F tar -X stream -z -P
docker compose cp postgres:/tmp/basebackup/. "$TARGET/"
docker compose exec -T postgres rm -rf /tmp/basebackup

echo "[$(date -u +%FT%TZ)] Created $TARGET" >> "$LOG"

# Upload to Backblaze B2
/usr/local/bin/tr_b2_upload.sh "$TARGET" || \
    echo "[$(date -u +%FT%TZ)] B2 upload failed" >> "$LOG"

# Rsync to laptop
if [[ -n "${LAPTOP_RSYNC_TARGET:-}" ]]; then
    rsync -avz --partial "$TARGET/" "$LAPTOP_RSYNC_TARGET/full_${TIMESTAMP}/" \
        && echo "[$(date -u +%FT%TZ)] Rsynced to laptop" >> "$LOG" \
        || echo "[$(date -u +%FT%TZ)] Rsync failed" >> "$LOG"
fi

# Retention: keep last 7 nightly fulls
find "$BACKUP_DIR" -maxdepth 1 -name "full_*" -mtime +7 -exec rm -rf {} \;
