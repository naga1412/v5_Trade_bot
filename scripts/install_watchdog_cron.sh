#!/bin/bash
# One-time installer for the trading-radar self-healing watchdog cron.
#
# Run this on the Hetzner host AFTER the deploy is in place + backup
# cron has been installed:
#   sudo ./scripts/install_watchdog_cron.sh
#
# Idempotent — safe to re-run; just refreshes the script copy and
# ensures the cron line exists exactly once. Schedule: every 15 min.

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/trading-radar}"
SCRIPT_DEST="$INSTALL_DIR/scripts"

if [ ! -d "$INSTALL_DIR" ]; then
  echo "✗ $INSTALL_DIR not found — run from a Hetzner host with the deploy in place" >&2
  exit 1
fi

echo "▶ installing watchdog.sh to $SCRIPT_DEST/"
mkdir -p "$SCRIPT_DEST"
copy_or_chmod() {
    local src="$1" dest="$2"
    if [ "$(realpath "$src")" = "$(realpath "$dest" 2>/dev/null || echo /nonexistent)" ]; then
        chmod 755 "$dest"
    else
        install -m 755 "$src" "$dest"
    fi
}
copy_or_chmod scripts/watchdog.sh "$SCRIPT_DEST/watchdog.sh"

touch /var/log/trading-radar-watchdog.log
chmod 644 /var/log/trading-radar-watchdog.log

# Cron — every 15 min.
CRON_LINE="*/15 * * * * $SCRIPT_DEST/watchdog.sh >> /var/log/trading-radar-watchdog.log 2>&1"
TMP_CRON=$(mktemp)
crontab -l 2>/dev/null | grep -v 'trading-radar.*watchdog' > "$TMP_CRON" || true
echo "$CRON_LINE" >> "$TMP_CRON"
crontab "$TMP_CRON"
rm "$TMP_CRON"
echo "✓ cron installed:"
crontab -l | grep watchdog

echo "▶ running watchdog.sh once with --dry-run to validate"
"$SCRIPT_DEST/watchdog.sh" --dry-run

echo "✓ watchdog ready. First real run at next */15 minute."
