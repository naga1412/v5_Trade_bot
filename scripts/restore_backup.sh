#!/bin/bash
# Restore an encrypted backup file produced by scripts/backup.sh.
#
# Usage:
#     scripts/restore_backup.sh <path-to-backup.sql.gz.enc> [target_db_name]
#
# Default target_db_name is "trading_radar_restore" (a *separate* DB from
# the live one). Restore-into-live requires manual override:
#     TARGET_IS_LIVE=1 scripts/restore_backup.sh <file> trading_radar
#
# Requires: BACKUP_ENCRYPTION_KEY env var (or sourced from .env in INSTALL_DIR).
#
# What it does:
#   1. Decrypt + gunzip the backup to a temp .sql file
#   2. Create the target DB if missing (drops if --drop-first passed)
#   3. psql < temp.sql
#   4. Print row counts of key tables
#   5. Clean up temp files
#
# Failure modes:
#   - Wrong encryption key → openssl exits with "bad decrypt"
#   - Target DB exists and TARGET_IS_LIVE!=1 → refuses (safety guard)
#   - psql restore errors → propagates exit code

set -euo pipefail

BACKUP_FILE="${1:?usage: $0 <path-to-backup.sql.gz.enc> [target_db_name]}"
TARGET_DB="${2:-trading_radar_restore}"
INSTALL_DIR="${INSTALL_DIR:-/opt/trading-radar}"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "[restore] ✗ file not found: $BACKUP_FILE" >&2
  exit 1
fi

if [ -f "$INSTALL_DIR/.env" ]; then
  set -a; . "$INSTALL_DIR/.env"; set +a
fi

if [ -z "${BACKUP_ENCRYPTION_KEY:-}" ]; then
  echo "[restore] ✗ BACKUP_ENCRYPTION_KEY not set" >&2
  exit 1
fi

# Safety guard against restoring on top of the live DB.
if [ "$TARGET_DB" = "${POSTGRES_DB:-trading_radar}" ] && [ "${TARGET_IS_LIVE:-0}" != "1" ]; then
  echo "[restore] ✗ refusing to restore on top of live DB '$TARGET_DB'" >&2
  echo "[restore]   set TARGET_IS_LIVE=1 to override (DESTROYS LIVE DATA)" >&2
  exit 1
fi

TMP=$(mktemp /tmp/tr_restore.XXXXXX.sql)
trap 'rm -f "$TMP"' EXIT

echo "[restore] ▶ decrypting + decompressing → $TMP"
openssl enc -d -aes-256-cbc -pbkdf2 -k "$BACKUP_ENCRYPTION_KEY" \
  -in "$BACKUP_FILE" | gunzip > "$TMP"

ROW_COUNT_QUERY="SELECT
  (SELECT count(*) FROM predictions) AS predictions,
  (SELECT count(*) FROM shadow_trades) AS shadow_trades,
  (SELECT count(*) FROM brain_decisions) AS brain_decisions,
  (SELECT count(*) FROM ml_checkpoints) AS ml_checkpoints,
  (SELECT count(*) FROM users) AS users;"

cd "$INSTALL_DIR"

# Create target DB. dropdb is idempotent with --if-exists.
if [ "${DROP_FIRST:-0}" = "1" ]; then
  echo "[restore] ▶ dropping existing $TARGET_DB if present"
  docker compose exec -T postgres dropdb -U postgres --if-exists "$TARGET_DB"
fi
echo "[restore] ▶ creating $TARGET_DB"
docker compose exec -T postgres createdb -U postgres "$TARGET_DB" 2>/dev/null || \
  echo "[restore]   (already exists; reusing)"

echo "[restore] ▶ piping SQL into postgres"
docker compose exec -T postgres psql -U postgres -d "$TARGET_DB" < "$TMP"

echo "[restore] ▶ row counts in $TARGET_DB:"
docker compose exec -T postgres psql -U postgres -d "$TARGET_DB" -c "$ROW_COUNT_QUERY"

echo "[restore] ✓ restored to '$TARGET_DB'. Cleanup: docker compose exec postgres dropdb -U postgres $TARGET_DB"
