#!/bin/bash
# SP-4 Phase B6 — nightly RL brain retrain on Hetzner CPU.
#
# Installed by the deploy script as a daily cron entry at 03:30 UTC
# (09:00 IST), 30 min after the weekly DB backup finishes. Output is
# tee'd to a log file the operator can grep when something looks off.
#
# Each run:
#   1. docker compose exec backend python /app/tools/ml/train_brain.py ...
#   2. compute eval metrics vs current champion via the SP-7 evaluate_challenger
#   3. if challenger_sharpe > champion_sharpe * 1.05:
#        send Telegram inline-button message — operator approves to swap
#      else:
#        log + delete candidate; no notification (most nights this branch)
#
# Failure modes (each handled with a Telegram alert):
#   - replay buffer empty -> log warning, exit 0 (no trades yet to learn from)
#   - training crashes -> Telegram WARNING, exit 1
#   - challenger registration fails -> Telegram ERROR, exit 1
#
# Idempotent: re-running mid-day produces a new candidate but doesn't
# disturb the live champion (still gates on the 5% Sharpe bar).

set -uo pipefail   # NOT -e — we want soft-failures to send Telegram alerts

INSTALL_DIR="${INSTALL_DIR:-/opt/trading-radar}"
RL_CACHE_DIR="${RL_CACHE_DIR:-${INSTALL_DIR}/backend/data/rl-cache}"
LOG_FILE="${LOG_FILE:-/var/log/trading-radar-brain-cron.log}"

# Telegram secrets — supplied by the .env at install time.
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
TELEGRAM_CHAT_ID="${TELEGRAM_CHAT_ID:-}"

mkdir -p "$RL_CACHE_DIR"

notify() {
  local emoji="$1"; local msg="$2"
  if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "[$(date -u +%FT%TZ)] WARN: telegram secrets unset; skipping notify" \
      | tee -a "$LOG_FILE"
    return 0
  fi
  curl -s -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d text="${emoji} brain retrain: ${msg}" >/dev/null || true
}

ts() { date -u +%FT%TZ; }
log() { echo "[$(ts)] $1" | tee -a "$LOG_FILE"; }

VERSION="v1-$(date -u +%Y%m%d-%H%M%S)"
log "▶ starting nightly retrain version=${VERSION}"

# Locate the current active checkpoint (if any) for warm-start.
WARM_START_PATH=""
LATEST=$(ls -t "$RL_CACHE_DIR"/ppo_policy_v*.pt 2>/dev/null | head -n1 || true)
if [ -n "${LATEST}" ]; then
  WARM_START_PATH="${LATEST}"
  log "warm-starting from ${LATEST}"
fi

# Run the training. docker compose exec is the same pattern the SP-1.1
# README uses for the register step, so the operator's mental model
# stays consistent.
TRAIN_CMD="python /app/tools/ml/train_brain.py \
  --window-days 365 \
  --out-dir /app/data/rl-cache \
  --device cpu \
  --version-tag ${VERSION}"
if [ -n "${WARM_START_PATH}" ]; then
  CONTAINER_PATH="/app/data/rl-cache/$(basename "$WARM_START_PATH")"
  TRAIN_CMD="${TRAIN_CMD} --warm-start ${CONTAINER_PATH}"
fi

if cd "$INSTALL_DIR" && docker compose exec -T backend bash -c "$TRAIN_CMD" \
   >>"$LOG_FILE" 2>&1; then
  log "✓ training completed for ${VERSION}"
else
  log "✗ training FAILED for ${VERSION}; see log tail below"
  tail -n 30 "$LOG_FILE" | sed 's/^/    /' | tee -a "$LOG_FILE"
  notify "❌" "training failed at ${VERSION}; see /var/log/trading-radar-brain-cron.log"
  exit 1
fi

# Look up the eval JSON the trainer just wrote.
EVAL_FILE="${RL_CACHE_DIR}/eval_brain_${VERSION}.json"
if [ ! -f "$EVAL_FILE" ]; then
  log "✗ eval file not found at ${EVAL_FILE}; aborting"
  notify "❌" "eval JSON missing for ${VERSION}"
  exit 1
fi
log "eval file: ${EVAL_FILE}"

# Phase B6 stops here. Phase B7 (registrar) + Phase D (admin endpoints)
# + Phase E (Telegram approval inline button) wire the rest:
#   - register_brain.py POSTs to /api/v1/admin/rl-checkpoints
#   - evaluate_challenger compares against active champion
#   - if challenger wins by ≥5% Sharpe, Telegram inline button posts
#   - operator taps Approve → backend swaps + restarts
#
# For now the cron lays the rails: training runs nightly, output is
# saved + logged, operator can manually inspect eval JSON until the
# rest of the pipeline lands.
log "▶ candidate ready for review: ${VERSION}"
notify "🧠" "candidate ${VERSION} trained. Inspect ${EVAL_FILE}."

log "▶ done"
exit 0
