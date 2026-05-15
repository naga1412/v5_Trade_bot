#!/bin/bash
# trading-radar Postgres backup — daily encrypted dump with optional offsite copy.
#
# Versioned location of the production cron entry installed at
# /opt/trading-radar/scripts/backup.sh on the Hetzner host. Reproduce by:
#   sudo cp scripts/backup.sh /opt/trading-radar/scripts/backup.sh
#   sudo crontab -l | grep -q backup.sh || (sudo crontab -l 2>/dev/null; echo '15 3 * * * /opt/trading-radar/scripts/backup.sh >> /var/log/trading-radar-backup.log 2>&1') | sudo crontab -
#
# Schedule: daily 03:15 UTC (was weekly Sunday 03:00 — too long an RPO).
#
# Output: /var/backups/trading-radar/backup_YYYY-MM-DD.sql.gz.enc (AES-256-CBC,
# key from BACKUP_ENCRYPTION_KEY env var). 14-day retention by default.
#
# Optional offsite copy: if BACKUP_B2_BUCKET is set in .env AND rclone is
# configured with a remote named "b2", uploads each new backup file to
# b2:${BACKUP_B2_BUCKET}/. The retention rule prunes ONLY local copies; B2
# lifecycle policy should handle remote retention.
#
# Restore: see scripts/restore_backup.sh
#
# Failure-mode: any step that fails sends a Telegram alert via the bot
# token in .env, then exits non-zero so cron records the failure.

set -uo pipefail   # NOT -e — we want to send Telegram on soft-failures

INSTALL_DIR="${INSTALL_DIR:-/opt/trading-radar}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/trading-radar}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-14}"

set -a; . "$INSTALL_DIR/.env"; set +a

DATE=$(date +%Y-%m-%d)
BACKUP_FILE="$BACKUP_DIR/backup_${DATE}.sql.gz.enc"
mkdir -p "$BACKUP_DIR"

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $1"; }

notify() {
  local emoji="$1"; local msg="$2"
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    log "WARN: telegram secrets unset; skipping notify ($emoji $msg)"
    return 0
  fi
  curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d text="${emoji} trading-radar backup: ${msg}" >/dev/null || true
}

# 1. Dump + encrypt locally.
log "▶ starting daily backup → ${BACKUP_FILE}"
if cd "$INSTALL_DIR" && \
   docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-trading_radar}" | \
   gzip | \
   openssl enc -aes-256-cbc -salt -pbkdf2 -k "${BACKUP_ENCRYPTION_KEY}" -out "$BACKUP_FILE"; then
  SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
  log "✓ local backup OK | $SIZE"
else
  log "✗ local backup FAILED"
  notify "❌" "FAILED $(ts); check /var/log/trading-radar-backup.log"
  exit 1
fi

# 2. Optional offsite copy via rclone → Backblaze B2 (or any rclone remote
#    named "b2"). No-op if BACKUP_B2_BUCKET is unset or rclone isn't installed.
OFFSITE_OK="skipped"
if [ -n "${BACKUP_B2_BUCKET:-}" ]; then
  if command -v rclone >/dev/null 2>&1; then
    log "▶ uploading to b2:${BACKUP_B2_BUCKET}/"
    if rclone copy "$BACKUP_FILE" "b2:${BACKUP_B2_BUCKET}/" --quiet --retries 3; then
      OFFSITE_OK="b2"
      log "✓ B2 upload OK"
    else
      log "✗ B2 upload FAILED"
      notify "⚠️" "local OK | B2 upload FAILED $(ts)"
      OFFSITE_OK="failed"
    fi
  else
    log "✗ BACKUP_B2_BUCKET set but rclone not installed; skipping offsite"
    notify "⚠️" "rclone missing; install + configure b2 remote"
  fi
fi

# 3. Local retention prune (RETENTION_DAYS rolling).
cd "$BACKUP_DIR"
PRUNED=$(find . -maxdepth 1 -name 'backup_*.sql.gz.enc' -mtime +${RETENTION_DAYS} -print -delete | wc -l)
[ "$PRUNED" -gt 0 ] && log "pruned ${PRUNED} backups older than ${RETENTION_DAYS} days"

# 4. Disk warning.
DISK_USED=$(df -h /var/backups | awk 'NR==2 {print $5}' | tr -d '%')
[ "$DISK_USED" -gt 80 ] && notify "⚠️" "Disk at ${DISK_USED}%"

# 5. Success notify with all signals.
notify "✅" "OK | size=${SIZE} | offsite=${OFFSITE_OK} | $(ts)"
log "▶ done"
exit 0
