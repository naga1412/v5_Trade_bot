#!/usr/bin/env bash
# Hourly incremental dump of changed tables. Runs on the Oracle host via cron.
set -euo pipefail

LOG_DIR="${LOG_DIR:-/var/log/trading-radar}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/trading-radar}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
KEEP_HOURS="${KEEP_HOURS:-72}"

mkdir -p "$BACKUP_DIR" "$LOG_DIR"
LOG="$LOG_DIR/pg_dump_hourly.log"

# Source env to get DATABASE_URL
set -a
[[ -f /home/ubuntu/trading-radar/.env ]] && . /home/ubuntu/trading-radar/.env
set +a

# Use docker exec since postgres is in a container
DUMP_FILE="$BACKUP_DIR/hourly_${TIMESTAMP}.sql.gz"

cd /home/ubuntu/trading-radar
docker compose exec -T postgres pg_dump \
    -U "${POSTGRES_USER:-postgres}" \
    -d "${POSTGRES_DB:-trading_radar}" \
    --data-only \
    --no-owner \
    --table=predictions \
    --table=paper_trades \
    --table=watchlist \
    --table=audit_violations \
    --table=data_quality_alerts \
    | gzip > "$DUMP_FILE"

echo "[$(date -u +%FT%TZ)] Wrote $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))" >> "$LOG"

# Prune older than KEEP_HOURS
find "$BACKUP_DIR" -name "hourly_*.sql.gz" -mmin +$((KEEP_HOURS * 60)) -delete
