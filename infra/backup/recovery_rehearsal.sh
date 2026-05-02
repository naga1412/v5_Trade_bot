#!/usr/bin/env bash
# Restore the latest B2 backup to a temporary postgres on the laptop and verify
# row counts match the production Oracle host. Run quarterly per §5.13.
set -euo pipefail

WORKDIR="${WORKDIR:-/tmp/tr_restore_$(date +%s)}"
TEMP_PORT="${TEMP_PORT:-5433}"
PG_PASS="${PG_PASS:-rehearsalpw}"

mkdir -p "$WORKDIR"
echo "[rehearsal] working in $WORKDIR"

# 1. Pull latest backup from B2
LATEST=$(rclone lsf b2:trading-radar-backups/ --dirs-only | grep '^full_' | sort | tail -1)
echo "[rehearsal] latest backup: $LATEST"
rclone copy "b2:trading-radar-backups/$LATEST" "$WORKDIR/restore" --progress

# 2. Start temp postgres
docker run -d --name tr-restore-pg \
    -e POSTGRES_PASSWORD="$PG_PASS" \
    -e POSTGRES_DB=trading_radar \
    -p $TEMP_PORT:5432 \
    -v "$WORKDIR/restore":/restore:ro \
    timescale/timescaledb:2.17.2-pg16

# Wait for ready
until docker exec tr-restore-pg pg_isready -U postgres; do sleep 2; done

# 3. Restore base backup
docker exec tr-restore-pg sh -c '
    cd /var/lib/postgresql/data && rm -rf ./*
    tar -xzf /restore/base.tar.gz -C /var/lib/postgresql/data
'
docker restart tr-restore-pg
until docker exec tr-restore-pg pg_isready -U postgres; do sleep 2; done

# 4. Compare row counts vs production
echo "[rehearsal] restored row counts:"
docker exec tr-restore-pg psql -U postgres -d trading_radar -c '
    SELECT
      (SELECT count(*) FROM predictions) AS predictions,
      (SELECT count(*) FROM paper_trades) AS paper_trades,
      (SELECT count(*) FROM ohlcv) AS ohlcv;
'

echo "[rehearsal] manually compare these to: ssh oracle 'docker compose exec postgres psql -U postgres trading_radar -c \"...\"'"
echo "[rehearsal] cleanup with: docker rm -f tr-restore-pg && rm -rf $WORKDIR"
