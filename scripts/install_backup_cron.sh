#!/bin/bash
# One-time installer for trading-radar backup cron on the Hetzner host.
#
# Run this from a freshly-cloned /opt/trading-radar checkout to:
#   1. Copy scripts/backup.sh + scripts/restore_backup.sh into place
#   2. Install (or refresh) the daily 03:15 UTC cron entry
#   3. Run backup.sh once manually to validate
#
# Idempotent: re-running just refreshes the script copies and ensures the
# cron line exists exactly once.
#
# Optional offsite (B2):
#   - sign up at https://backblaze.com/b2 (free tier covers this use case)
#   - create a bucket (e.g. "trading-radar-backups")
#   - create an application key with write access to that bucket
#   - install rclone:        curl -fsSL https://rclone.org/install.sh | bash
#   - configure b2 remote:   rclone config  (name it "b2")
#   - add to /opt/trading-radar/.env:
#         BACKUP_B2_BUCKET=trading-radar-backups
#   - re-run this installer; the next backup will upload automatically

set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/trading-radar}"
SCRIPT_DEST="$INSTALL_DIR/scripts"

if [ ! -d "$INSTALL_DIR" ]; then
  echo "✗ $INSTALL_DIR not found — run from a Hetzner host with the deploy in place" >&2
  exit 1
fi

echo "▶ installing backup scripts to $SCRIPT_DEST/"
mkdir -p "$SCRIPT_DEST"
install -m 755 scripts/backup.sh         "$SCRIPT_DEST/backup.sh"
install -m 755 scripts/restore_backup.sh "$SCRIPT_DEST/restore_backup.sh"

# Ensure log file is writable.
touch /var/log/trading-radar-backup.log
chmod 644 /var/log/trading-radar-backup.log

# Cron entry — daily at 03:15 UTC.
CRON_LINE="15 3 * * * $SCRIPT_DEST/backup.sh >> /var/log/trading-radar-backup.log 2>&1"
TMP_CRON=$(mktemp)
crontab -l 2>/dev/null | grep -v 'trading-radar.*backup' > "$TMP_CRON" || true
echo "$CRON_LINE" >> "$TMP_CRON"
crontab "$TMP_CRON"
rm "$TMP_CRON"
echo "✓ cron installed:"
crontab -l | grep backup

# Sanity check — try one run NOW so we catch missing env/key issues immediately.
echo "▶ running backup.sh once to validate"
if "$SCRIPT_DEST/backup.sh"; then
  echo "✓ backup.sh exit OK; check /var/backups/trading-radar/"
  ls -lh /var/backups/trading-radar/ | tail -3
else
  echo "✗ backup.sh failed; inspect /var/log/trading-radar-backup.log" >&2
  tail -20 /var/log/trading-radar-backup.log
  exit 1
fi
