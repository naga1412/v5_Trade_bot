#!/bin/bash
# SP-4 Phase B6 — nightly RL brain retrain on Hetzner CPU.
#
# Installed by the deploy script as a daily cron entry at 03:30 UTC
# (09:00 IST), 30 min after the weekly DB backup finishes. Output is
# tee'd to a log file the operator can grep when something looks off.
#
# Each run:
#   1. docker compose exec backend python /app/host-tools/ml/train_brain.py ...
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

# Source .env so crontab environments inherit TELEGRAM_* and other secrets.
if [ -f "${INSTALL_DIR}/.env" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${INSTALL_DIR}/.env"
  set +a
fi

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
  # 2026-08-05: the curl response + exit code used to be swallowed
  # (`>/dev/null || true`) — a delivery failure (bad token, Telegram API
  # rejecting the request, network blip) was invisible: the log would
  # simply have no notify-related line at all, identical to the happy
  # path. Capture both so a silent-delivery-failure is distinguishable
  # from "notify was never called" when reading the log after the fact.
  local resp_file="/tmp/brain_cron_notify_resp.$$"
  local http_code
  http_code=$(curl -s -o "$resp_file" -w '%{http_code}' -X POST \
    "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    -d chat_id="${TELEGRAM_CHAT_ID}" \
    -d text="${emoji} brain retrain: ${msg}") || true
  if [ "$http_code" != "200" ]; then
    echo "[$(date -u +%FT%TZ)] WARN: telegram notify FAILED (http_code=${http_code:-none}): $(cat "$resp_file" 2>/dev/null)" \
      | tee -a "$LOG_FILE"
  fi
  rm -f "$resp_file"
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
TRAIN_CMD="python /app/host-tools/ml/train_brain.py \
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

# Phase D: register the candidate and run the Sharpe gate. --activate
# triggers champion-challenger logic in register_brain.py: if no champion
# exists (bootstrap), the new checkpoint activates immediately. If a
# champion exists, the challenger must exceed champion_sharpe * 1.05 to
# swap in; otherwise it registers as is_active=False with a "gate_held"
# marker. --direct bypasses the HTTP route (gated by Cloudflare Access
# JWT; the cron has no bearer token).
log "▶ Phase D — registering candidate in rl_checkpoints (Sharpe gate)"
REGISTER_CMD="python /app/host-tools/ml/register_brain.py \
  --checkpoint /app/data/rl-cache/ppo_policy_${VERSION}.pt \
  --eval /app/data/rl-cache/eval_brain_${VERSION}.json \
  --direct \
  --activate"

REGISTER_OUT=$(cd "$INSTALL_DIR" && docker compose exec -T backend bash -c "$REGISTER_CMD" 2>&1 || true)
echo "$REGISTER_OUT" >> "$LOG_FILE"
CKPT_ID=$(echo "$REGISTER_OUT" | grep -oE 'id=[0-9]+' | head -n1 | cut -d'=' -f2)
if [ -z "$CKPT_ID" ]; then
  log "✗ register_brain.py did not return a checkpoint id; manual promotion required"
  notify "⚠️" "candidate ${VERSION} trained but registration failed; check ${LOG_FILE}"
  exit 1
fi
log "✓ registered as rl_checkpoints.id=${CKPT_ID}"

# Phase E: notify operator of gate outcome and restart backend if activated.
log "▶ Phase E — Telegram notification"
if echo "$REGISTER_OUT" | grep -q 'gate_held:'; then
  notify "🧠" "candidate ${VERSION} (id=${CKPT_ID}) registered but gate_held: Sharpe did not beat champion by >5%. Manual PATCH to activate if desired."
elif echo "$REGISTER_OUT" | grep -q 'gate_undecidable:'; then
  notify "🧠" "candidate ${VERSION} (id=${CKPT_ID}) registered but gate_undecidable: champion Sharpe missing; manual review required."
else
  # Checkpoint was activated (bootstrap or gate_passed). Restart backend so
  # the new policy loads from DB on the next startup.
  log "▶ restarting backend to load id=${CKPT_ID}"
  if cd "$INSTALL_DIR" && docker compose restart backend >>"$LOG_FILE" 2>&1; then
    log "✓ backend restarted — id=${CKPT_ID} active on next prediction"
    notify "🧠" "candidate ${VERSION} (id=${CKPT_ID}) activated + backend restarted. Brain is live."
  else
    log "✗ backend restart failed; manual restart required"
    notify "⚠️" "candidate ${VERSION} (id=${CKPT_ID}) activated but backend restart FAILED. Run: docker compose restart backend"
  fi
fi

log "▶ done"
exit 0
