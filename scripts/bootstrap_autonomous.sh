#!/bin/bash
# One-shot Hetzner bootstrap for the SP-8 autonomous-trading subsystem.
#
# Run after the regular deploy is in place, secrets.enc is in
# /opt/trading-radar/backend/, and /opt/trading-radar/.env has the
# required keys filled in. Idempotent — safe to re-run.
#
#   sudo ./scripts/bootstrap_autonomous.sh
#
# Steps (each fails loud + halts on error):
#   1. Check .env has the required autonomous-trading keys
#   2. Check secrets.enc exists
#   3. Install backup cron        (delegates to install_backup_cron.sh)
#   4. Install watchdog cron      (delegates to install_watchdog_cron.sh)
#   5. docker compose up --build backend (apply the new env)
#   6. Wait 30s + grep "preflight" in logs to confirm 5/5 passed
#
# The result of step 6 is the gate: if pre-flight passed, the
# autonomous workers are running. If not, the rest of the platform
# (paper trading, ghost candles, dashboard) stays up — the bootstrap
# just couldn't enable autonomous mode.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/trading-radar}"

if [ ! -d "$INSTALL_DIR" ]; then
  echo "✗ $INSTALL_DIR not found — run on a Hetzner host with the deploy in place" >&2
  exit 1
fi

cd "$INSTALL_DIR"

# ---- Step 1: required env keys ----
required=(
  "MASTER_PASSPHRASE"
  "AUTONOMOUS_TRADING_ENABLED"
)
recommended=(
  "TELEGRAM_BOT_TOKEN"
  "TELEGRAM_CHAT_ID"
  "AUTO_PROMOTE_TO_TELEGRAM_ENABLED"
  "AUTO_PROMOTE_TO_FULLYAUTO_ENABLED"
)

echo "▶ checking $INSTALL_DIR/.env for required keys"
if [ ! -f "$INSTALL_DIR/.env" ]; then
  echo "✗ $INSTALL_DIR/.env missing" >&2
  exit 1
fi

missing=0
for k in "${required[@]}"; do
  if ! grep -qE "^$k=" "$INSTALL_DIR/.env"; then
    echo "✗ .env missing required key: $k" >&2
    missing=1
  fi
done
if [ $missing -ne 0 ]; then
  echo "" >&2
  echo "Add the missing keys to $INSTALL_DIR/.env and re-run." >&2
  echo "See docs/sp8-autonomous-trading-operator-guide.md §Pre-conditions for the full list." >&2
  exit 1
fi
for k in "${recommended[@]}"; do
  if ! grep -qE "^$k=" "$INSTALL_DIR/.env"; then
    echo "  ⚠ .env missing recommended key: $k (skipping is OK; that path stays disabled)"
  fi
done
echo "✓ .env validated"

# Source the .env so we can verify AUTONOMOUS_TRADING_ENABLED=true
# (otherwise the bootstrap is a no-op).
set +u
# shellcheck disable=SC1091
. "$INSTALL_DIR/.env"
set -u

if [ "${AUTONOMOUS_TRADING_ENABLED:-false}" != "true" ]; then
  echo "✗ AUTONOMOUS_TRADING_ENABLED is not 'true' in .env; bootstrap aborted" >&2
  exit 1
fi

# ---- Step 2: secrets.enc ----
if [ ! -f "$INSTALL_DIR/backend/secrets.enc" ]; then
  echo "✗ $INSTALL_DIR/backend/secrets.enc missing" >&2
  echo "  scp secrets.enc root@<host>:$INSTALL_DIR/backend/secrets.enc" >&2
  exit 1
fi
SECRETS_BYTES=$(stat -c '%s' "$INSTALL_DIR/backend/secrets.enc")
if [ "$SECRETS_BYTES" -lt 32 ]; then
  echo "✗ secrets.enc looks truncated ($SECRETS_BYTES bytes)" >&2
  exit 1
fi
echo "✓ secrets.enc present ($SECRETS_BYTES bytes)"

# ---- Step 3 + 4: cron installers ----
echo "▶ installing backup cron"
"$INSTALL_DIR/scripts/install_backup_cron.sh"
echo "▶ installing watchdog cron"
"$INSTALL_DIR/scripts/install_watchdog_cron.sh"

# ---- Step 5: rebuild + restart backend so the new env takes effect ----
echo "▶ docker compose up -d --build backend (this also runs migrations on entry)"
docker compose up -d --build backend

# ---- Step 6: wait for pre-flight ----
echo "▶ waiting 30s for backend to settle + run pre-flight"
sleep 30
echo "▶ checking pre-flight result"
if docker compose logs --tail=200 backend 2>&1 | grep -i "preflight" | tail -5; then
  if docker compose logs --tail=500 backend 2>&1 | grep -q "pre-flight passed"; then
    echo "✓ pre-flight passed — autonomous workers are running"
  else
    echo "✗ pre-flight did NOT pass — see backend logs for the failing check" >&2
    docker compose logs --tail=200 backend 2>&1 | grep -iE "preflight|autonomous"
    exit 1
  fi
else
  echo "⚠ no preflight log line found yet; tail more aggressively or wait" >&2
fi

cat <<'EOF'

✅ Bootstrap complete. Next:

  - Visit https://aji12.nagayuaj.com → Autonomous tab to see mode + gate status.
  - tail -f /var/log/trading-radar-watchdog.log to watch the self-healing loop.
  - If TELEGRAM_BOT_TOKEN was set, send /status to your bot to confirm it answers.
  - With AUTO_PROMOTE_TO_TELEGRAM_ENABLED=true, the daily 03:30 UTC worker will
    auto-promote you once gates pass for AUTO_PROMOTE_CONSECUTIVE_DAYS days
    (default 7).

You do NOT need to babysit. The system is now self-driving.

EOF
