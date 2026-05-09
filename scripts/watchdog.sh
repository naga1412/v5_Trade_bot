#!/bin/bash
# Self-healing watchdog for trading-radar.
#
# Runs every 15 min via cron. For each known failure mode: detect,
# attempt auto-fix, and Telegram-alert. Idempotent — safe to run on
# repeat.
#
# Designed for the case where the operator can't be available to debug.
# Covers ~80% of common incidents (crash loop, OOM, disk full, stale
# predictions, missing backup, Binance outage). Anything outside that
# set gets a loud Telegram alert so the operator knows to investigate.
#
# Telegram credentials come from /opt/trading-radar/.env. Without them
# the watchdog still runs + auto-fixes but only logs (no alerts).
#
# Install via: sudo ./scripts/install_watchdog_cron.sh

set -uo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/trading-radar}"
LOG_FILE="${LOG_FILE:-/var/log/trading-radar-watchdog.log}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/trading-radar}"

set -a; [ -f "$INSTALL_DIR/.env" ] && . "$INSTALL_DIR/.env"; set +a

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
  DRY_RUN=1
fi

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $1" | tee -a "$LOG_FILE"; }

# Track findings so we send one summary alert per run.
ALERTS=()
ACTIONS=()

notify_telegram() {
  local emoji="$1"; local msg="$2"
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
    log "telegram unset; skipping notify ($emoji $msg)"
    return 0
  fi
  curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    --data-urlencode "text=${emoji} watchdog: ${msg}" >/dev/null || true
}

run() {
  # Wraps actions so --dry-run can show what would happen.
  if [ "$DRY_RUN" = "1" ]; then
    log "[DRY] would: $*"
  else
    log "EXEC: $*"
    "$@" >> "$LOG_FILE" 2>&1 || log "  exit=$?"
  fi
}

# ============================================================
# Check 1: Container health (restart loop or down)
# ============================================================
check_containers() {
  local restart_count
  restart_count=$(docker inspect tr-backend --format '{{.RestartCount}}' 2>/dev/null || echo 0)
  local status
  status=$(docker inspect tr-backend --format '{{.State.Status}}' 2>/dev/null || echo missing)

  if [ "$status" = "missing" ]; then
    ALERTS+=("backend container missing")
    run docker compose -f "$INSTALL_DIR/docker-compose.yml" up -d backend
    ACTIONS+=("started backend container")
    return
  fi

  if [ "$status" = "restarting" ]; then
    ALERTS+=("backend in restart loop")
    # Don't auto-fix — restart-loop usually means a code-level bug.
    # Operator must investigate via logs.
    return
  fi

  if [ "$restart_count" -gt 5 ]; then
    ALERTS+=("backend RestartCount=$restart_count in 24h")
  fi
}

# ============================================================
# Check 2: Memory pressure (>90% used → restart backend)
# ============================================================
check_memory() {
  local pct
  pct=$(free | awk '/^Mem:/ {printf "%d", ($3/$2)*100}')
  if [ "$pct" -gt 90 ]; then
    ALERTS+=("memory ${pct}% used")
    run docker compose -f "$INSTALL_DIR/docker-compose.yml" restart backend
    ACTIONS+=("restarted backend (memory ${pct}%)")
  fi
}

# ============================================================
# Check 3: Disk space (>80% prune; >95% emergency)
# ============================================================
check_disk() {
  local used
  used=$(df /var/lib/docker | awk 'NR==2 {print $5}' | tr -d '%')

  if [ "$used" -gt 95 ]; then
    ALERTS+=("DOCKER DISK ${used}% — EMERGENCY")
    run docker system prune -af --filter "until=24h"
    run find "$BACKUP_DIR" -name 'backup_*.sql.gz.enc' -mtime +7 -delete
    ACTIONS+=("docker prune + backup retention reduced")
  elif [ "$used" -gt 80 ]; then
    ALERTS+=("docker disk ${used}%")
    run docker system prune -f --filter "until=72h"
    run find "$BACKUP_DIR" -name 'backup_*.sql.gz.enc' -mtime +14 -delete
    ACTIONS+=("docker prune + 14d backup retention")
  fi
}

# ============================================================
# Check 4: Predictions stale (no writes in last 2h → restart worker)
# ============================================================
check_predictions_fresh() {
  local count
  count=$(docker exec tr-postgres psql -U postgres -d trading_radar -t -c \
    "SELECT count(*) FROM predictions WHERE ts > now() - interval '2 hours';" 2>/dev/null \
    | tr -d ' ' || echo 0)

  # Skip if Postgres itself is unreachable (logged separately).
  if [ -z "$count" ]; then
    return
  fi

  if [ "$count" = "0" ]; then
    ALERTS+=("no predictions written in 2h (live worker stuck?)")
    run docker compose -f "$INSTALL_DIR/docker-compose.yml" restart backend
    ACTIONS+=("restarted backend (stale predictions)")
  fi
}

# ============================================================
# Check 5: Backup freshness (>25h → manual run)
# ============================================================
check_backup_fresh() {
  local newest
  newest=$(find "$BACKUP_DIR" -name 'backup_*.sql.gz.enc' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | head -n 1 | cut -d' ' -f2 || echo "")

  if [ -z "$newest" ]; then
    ALERTS+=("no backup files in $BACKUP_DIR")
    if [ -x "$INSTALL_DIR/scripts/backup.sh" ]; then
      run "$INSTALL_DIR/scripts/backup.sh"
      ACTIONS+=("ran backup.sh (no prior backup found)")
    fi
    return
  fi

  local age_hours
  age_hours=$(( ( $(date +%s) - $(stat -c %Y "$newest") ) / 3600 ))
  if [ "$age_hours" -gt 25 ]; then
    ALERTS+=("newest backup is ${age_hours}h old")
    if [ -x "$INSTALL_DIR/scripts/backup.sh" ]; then
      run "$INSTALL_DIR/scripts/backup.sh"
      ACTIONS+=("ran backup.sh (was ${age_hours}h stale)")
    fi
  fi
}

# ============================================================
# Check 6: Binance reachable (info only — kill switches handle the rest)
# ============================================================
check_binance() {
  if ! curl -sf -o /dev/null -m 8 https://fapi.binance.com/fapi/v1/ping; then
    ALERTS+=("Binance Futures API unreachable from Hetzner")
    # Don't restart — kill switches in the backend handle the freeze.
    # Watchdog just elevates visibility.
  fi
}

# ============================================================
# Check 7: Postgres reachable
# ============================================================
check_postgres() {
  if ! docker exec tr-postgres pg_isready -U postgres -t 5 >/dev/null 2>&1; then
    ALERTS+=("Postgres not ready")
    run docker compose -f "$INSTALL_DIR/docker-compose.yml" restart postgres
    ACTIONS+=("restarted postgres")
  fi
}

# ============================================================
# MAIN
# ============================================================
log "▶ watchdog tick$([ $DRY_RUN = 1 ] && echo ' (dry-run)')"

check_containers
check_postgres
check_memory
check_disk
check_predictions_fresh
check_backup_fresh
check_binance

if [ "${#ALERTS[@]}" -eq 0 ]; then
  log "✓ all clear"
  exit 0
fi

# Build summary message.
SUMMARY="${#ALERTS[@]} alert(s):"$'\n'
for a in "${ALERTS[@]}"; do
  SUMMARY+="• $a"$'\n'
done
if [ "${#ACTIONS[@]}" -gt 0 ]; then
  SUMMARY+="Actions taken:"$'\n'
  for a in "${ACTIONS[@]}"; do
    SUMMARY+="→ $a"$'\n'
  done
fi

log "ALERTS: ${ALERTS[*]}"
log "ACTIONS: ${ACTIONS[*]}"
notify_telegram "🚨" "$SUMMARY"

exit 0
