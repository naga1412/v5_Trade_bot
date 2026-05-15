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

# Cross-tick state. Two files:
#   prev-alerts → list of alerts that fired last tick, one per line.
#                 Used to edge-trigger NEW / RESOLVED / PERSISTING.
#   heartbeat   → unix timestamp of the last all-clear daily summary
#                 send, so we send the "all systems clean" Telegram at
#                 most once per 24h.
STATE_DIR="${STATE_DIR:-/var/lib/trading-radar}"
PREV_ALERTS_FILE="$STATE_DIR/watchdog-prev-alerts"
HEARTBEAT_FILE="$STATE_DIR/watchdog-last-heartbeat"
mkdir -p "$STATE_DIR"

# How long between "all clear" daily heartbeat Telegrams. 24h is the
# right cadence — frequent enough to confirm the watchdog itself is
# alive, sparse enough not to spam.
HEARTBEAT_INTERVAL_SECONDS=$((24 * 60 * 60))

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
# Check 4: Predictions table activity (informational only — DO NOT auto-restart)
# ============================================================
# The live worker is per-user-WebSocket-subscription, NOT a cron. It only
# writes predictions when a user is actively watching a symbol on the
# dashboard. Empty rows in the last 2h is the EXPECTED behavior when
# nobody is viewing the app — NOT a sign that anything is stuck.
#
# Previous revision auto-restarted the backend on this signal. That caused
# a restart loop every ~15 min whenever the operator was away from the
# dashboard:
#   - watchdog sees 0 predictions in 2h → restarts container
#   - new container has no active WS subscribers → still 0 predictions
#   - 15 min later, restart again
# The shadow worker (which holds open paper-trade positions) survives
# restart but the multi-stream WS reconnects can drop in-flight candle
# bars. Several overnight restarts on 2026-05-11/12 triggered this loop.
#
# The right "is the trading engine stuck" signal is shadow_open_positions
# transitions or the worker_heartbeats table (Python worker_watchdog).
# Both are tracked elsewhere — no need to alert here.
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
    # Log-only — no Telegram alert, no auto-restart. This is informational.
    log "predictions_fresh: 0 rows in last 2h (expected if dashboard not in use)"
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
# Check 8: HTTP /api/v1/health endpoint
# ============================================================
# The container can be "running" while uvicorn itself is wedged
# (e.g. event loop blocked on a sync call). Docker only sees the
# process is up; the user sees a hung dashboard. A direct HTTP
# probe distinguishes these two states cheaply.
check_health_endpoint() {
  if ! curl -sf -o /dev/null -m 10 \
       --retry 3 --retry-delay 2 --retry-connrefused \
       http://localhost:8000/api/v1/health; then
    ALERTS+=("/api/v1/health not responding")
    run docker compose -f "$INSTALL_DIR/docker-compose.yml" restart backend
    ACTIONS+=("restarted backend (health endpoint unresponsive)")
  fi
}

# ============================================================
# Check 9: In-process watchdog heartbeat is fresh
# ============================================================
# The in-process worker_watchdog is responsible for auto-restarting
# stale non-stateful workers (scanner, validator, news_ingest, ...).
# If the watchdog itself goes silent, the self-healing loop dies — at
# that point only a backend container restart resurrects it. The
# host-level watchdog (this script) is the outermost safety net that
# catches "the in-process watchdog is dead" — a meta-check the
# in-process layer fundamentally cannot do.
#
# Threshold: 20 minutes = 4x WATCHDOG_INTERVAL_SECONDS (5min). Anything
# under that is normal jitter. Anything over it means the watchdog has
# missed at least 3 consecutive ticks — backend restart is justified.
check_in_process_watchdog() {
  local age_seconds
  age_seconds=$(docker exec tr-postgres psql -U postgres -d trading_radar -t -c \
    "SELECT EXTRACT(EPOCH FROM (NOW() - beat_at))::int FROM worker_heartbeats \
     WHERE worker_name='worker_watchdog_task';" 2>/dev/null \
    | tr -d ' ' || echo "")

  if [ -z "$age_seconds" ]; then
    return  # Postgres unreachable — check_postgres handles it.
  fi

  if ! [[ "$age_seconds" =~ ^[0-9]+$ ]]; then
    # No row → watchdog has never beaten on this deploy. Treat as DEAD
    # only after a generous startup grace via the threshold below.
    age_seconds=99999
  fi

  if [ "$age_seconds" -gt 1200 ]; then
    ALERTS+=("in-process watchdog silent for ${age_seconds}s")
    run docker compose -f "$INSTALL_DIR/docker-compose.yml" restart backend
    ACTIONS+=("restarted backend (in-process watchdog dead)")
  fi
}

# ============================================================
# MAIN
# ============================================================
log "▶ watchdog tick$([ $DRY_RUN = 1 ] && echo ' (dry-run)')"

check_containers
check_postgres
check_health_endpoint
check_in_process_watchdog
check_memory
check_disk
check_predictions_fresh
check_backup_fresh
check_binance

# ============================================================
# Edge-triggered notification: diff current alerts vs last tick.
# ============================================================
# Without edge triggering, a stuck-but-persistent alert (e.g. backup
# stale and backup.sh keeps failing) sends a Telegram EVERY 15 min,
# 96 messages a day — noise that trains the operator to ignore alerts.
# Edge triggering sends only on state CHANGE: new failures + recoveries.
PREV_ALERTS=()
if [ -f "$PREV_ALERTS_FILE" ]; then
  mapfile -t PREV_ALERTS < "$PREV_ALERTS_FILE"
fi

# alert key = the leading word up to the first space/colon. Lets us match
# "memory 92% used" against last tick's "memory 95% used" as the same
# issue, instead of treating each percentage drift as new+resolved+new.
alert_key() {
  echo "${1%% *}"
}

CURRENT_KEYS=()
for a in "${ALERTS[@]}"; do
  CURRENT_KEYS+=("$(alert_key "$a")")
done
PREV_KEYS=()
for p in "${PREV_ALERTS[@]}"; do
  PREV_KEYS+=("$(alert_key "$p")")
done

NEW_ALERTS=()
PERSISTING_ALERTS=()
for i in "${!ALERTS[@]}"; do
  if printf '%s\n' "${PREV_KEYS[@]}" | grep -qxF "${CURRENT_KEYS[$i]}"; then
    PERSISTING_ALERTS+=("${ALERTS[$i]}")
  else
    NEW_ALERTS+=("${ALERTS[$i]}")
  fi
done
RESOLVED_ALERTS=()
for j in "${!PREV_ALERTS[@]}"; do
  if ! printf '%s\n' "${CURRENT_KEYS[@]}" | grep -qxF "${PREV_KEYS[$j]}"; then
    RESOLVED_ALERTS+=("${PREV_ALERTS[$j]}")
  fi
done

# Persist current alerts for next tick's diff.
printf '%s\n' "${ALERTS[@]}" > "$PREV_ALERTS_FILE"

# ============================================================
# Telegram dispatch (one message per state-change category).
# ============================================================

# ✅ Recoveries: previous-tick alerts that are gone this tick. This is
# the positive-confirmation signal the operator asked for — silence
# alone is ambiguous (could be watchdog crashed), an explicit ✅ Telegram
# is unambiguous.
if [ "${#RESOLVED_ALERTS[@]}" -gt 0 ]; then
  RESOLVED_MSG="${#RESOLVED_ALERTS[@]} resolved:"$'\n'
  for r in "${RESOLVED_ALERTS[@]}"; do
    RESOLVED_MSG+="✓ $r"$'\n'
  done
  notify_telegram "✅" "self-heal succeeded — $RESOLVED_MSG"
  log "RESOLVED: ${RESOLVED_ALERTS[*]}"
fi

# 🚨 New alerts: first tick this issue has appeared. Always send with
# any heal actions the watchdog took.
if [ "${#NEW_ALERTS[@]}" -gt 0 ]; then
  NEW_MSG="${#NEW_ALERTS[@]} new alert(s):"$'\n'
  for a in "${NEW_ALERTS[@]}"; do
    NEW_MSG+="• $a"$'\n'
  done
  if [ "${#ACTIONS[@]}" -gt 0 ]; then
    NEW_MSG+="Actions taken:"$'\n'
    for a in "${ACTIONS[@]}"; do
      NEW_MSG+="→ $a"$'\n'
    done
  fi
  notify_telegram "🚨" "$NEW_MSG"
  log "NEW: ${NEW_ALERTS[*]}"
fi

# ⏱ Persisting alerts: same issue as last tick. We do NOT spam Telegram
# on every persisting tick — that's the noise we're trying to eliminate.
# But we DO log it so the watchdog log shows what's stuck, and we ESCALATE
# to Telegram once we cross a "stuck for 1 hour" threshold (4 ticks).
# Tracked via a counter file per alert key.
PERSIST_DIR="$STATE_DIR/persist-counts"
mkdir -p "$PERSIST_DIR"
for i in "${!PERSISTING_ALERTS[@]}"; do
  key="${PERSISTING_ALERTS[$i]%% *}"
  count_file="$PERSIST_DIR/$key"
  count=$(cat "$count_file" 2>/dev/null || echo 0)
  count=$((count + 1))
  echo "$count" > "$count_file"
  if [ "$count" = "4" ]; then
    # 4 consecutive 15-min ticks = 1h of being stuck. Re-alert once.
    notify_telegram "⏱" "alert STUCK for 1h, manual investigation needed: ${PERSISTING_ALERTS[$i]}"
    log "PERSIST-ESCALATE: ${PERSISTING_ALERTS[$i]}"
  fi
done
# Clear persist counters for keys that resolved this tick.
for r in "${RESOLVED_ALERTS[@]}"; do
  rm -f "$PERSIST_DIR/${r%% *}"
done
if [ "${#PERSISTING_ALERTS[@]}" -gt 0 ]; then
  log "PERSISTING (no spam): ${PERSISTING_ALERTS[*]}"
fi

# ============================================================
# Daily heartbeat — positive "watchdog is alive" signal.
# ============================================================
# If we have zero alerts AND zero actions this tick AND we haven't sent
# a heartbeat in 24h, send one. Operator gets a single "✓ N ticks clean"
# Telegram per day so silence is unambiguous.
if [ "${#ALERTS[@]}" -eq 0 ] && [ "${#ACTIONS[@]}" -eq 0 ]; then
  log "✓ all clear"
  last_hb=$(cat "$HEARTBEAT_FILE" 2>/dev/null || echo 0)
  now_unix=$(date +%s)
  age=$((now_unix - last_hb))
  if [ "$age" -ge "$HEARTBEAT_INTERVAL_SECONDS" ]; then
    notify_telegram "💚" "all systems clean — heartbeat $(ts)"
    echo "$now_unix" > "$HEARTBEAT_FILE"
    log "heartbeat sent"
  fi
fi

# Always log a summary line.
log "DONE: alerts=${#ALERTS[@]} new=${#NEW_ALERTS[@]} persisting=${#PERSISTING_ALERTS[@]} resolved=${#RESOLVED_ALERTS[@]} actions=${#ACTIONS[@]}"

exit 0
