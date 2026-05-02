#!/usr/bin/env bash
# Uploads a directory to B2 using rclone.
set -euo pipefail

SRC="${1:?usage: $0 <source-dir>}"
DEST="b2:${B2_BUCKET}/$(basename "$SRC")"

if ! command -v rclone >/dev/null 2>&1; then
    curl -fsSL https://rclone.org/install.sh | sudo bash
fi

# rclone config must already exist at /root/.config/rclone/rclone.conf with a
# remote named "b2" — see infra/backup/README.md
rclone copy "$SRC" "$DEST" --progress --retries 3 --transfers 4
