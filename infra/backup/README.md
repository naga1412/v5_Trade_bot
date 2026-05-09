# Backup & Recovery

## Current setup (Hetzner)

| What | Where | When |
|---|---|---|
| Daily encrypted dump | `/var/backups/trading-radar/backup_YYYY-MM-DD.sql.gz.enc` on Hetzner | 03:15 UTC daily |
| Optional offsite copy | `b2:${BACKUP_B2_BUCKET}/` via rclone | same run, after local dump |

**RPO**: 24h (worst case = data lost since last 03:15 UTC dump)
**RTO**: ~1 hour (decrypt → docker exec psql restore)

## Files

- [`scripts/backup.sh`](../../scripts/backup.sh) — daily cron. `pg_dump` whole DB → gzip → AES-256-CBC encrypt → write to `/var/backups/trading-radar/`. Optional B2 upload. Telegram alerts on success/failure. 14-day local retention.
- [`scripts/restore_backup.sh`](../../scripts/restore_backup.sh) — restore an encrypted dump into a side-channel DB (defaults to `trading_radar_restore`). Refuses to overwrite the live DB unless `TARGET_IS_LIVE=1` is set.
- [`scripts/install_backup_cron.sh`](../../scripts/install_backup_cron.sh) — one-time installer: copies the scripts, installs the cron entry, runs one validation backup.

## Setup on a fresh Hetzner host

```bash
# After git clone /opt/trading-radar and docker compose up
cd /opt/trading-radar
sudo ./scripts/install_backup_cron.sh
```

## Enabling offsite (Backblaze B2)

The default setup keeps backups on Hetzner only — if the box dies, backups die with it. Add an offsite copy in 5 minutes:

1. Sign up at <https://backblaze.com/b2> (free tier — 10 GB storage, 1 GB/day egress)
2. Create a bucket (e.g. `trading-radar-backups`); set lifecycle to keep 30 versions
3. Create an application key with **write** scope to that bucket
4. On Hetzner: `curl -fsSL https://rclone.org/install.sh | sudo bash`
5. `rclone config` → choose `n` (new) → name `b2` → choose Backblaze B2 → paste your key/secret
6. Edit `/opt/trading-radar/.env`:
   ```
   BACKUP_B2_BUCKET=trading-radar-backups
   ```
7. Re-run `sudo ./scripts/install_backup_cron.sh` to validate the next backup uploads.

## Restore — quarterly rehearsal

On any host with docker compose + the encryption key:

```bash
# Pull the latest encrypted backup (from Hetzner or B2)
scp -i ~/.ssh/oracle_key root@95.216.187.204:/var/backups/trading-radar/backup_$(date +%F).sql.gz.enc .

# Restore into a SIDE-CHANNEL DB (safe — won't touch live)
INSTALL_DIR=/opt/trading-radar BACKUP_ENCRYPTION_KEY=... \
  ./scripts/restore_backup.sh ./backup_$(date +%F).sql.gz.enc

# Compare row counts vs live (printed by restore_backup.sh)
# Then drop the restore DB:
docker compose exec postgres dropdb -U postgres trading_radar_restore
```

## Failure-mode plan

- **Hetzner box dies + offsite enabled** → fresh server, `git clone`, `docker compose up`, `restore_backup.sh` from B2 → flip Cloudflare Tunnel to new box. RTO ~2 hours.
- **Hetzner dies + offsite disabled** → all data lost since deploy. **Set up B2 if you care.**
- **B2 unavailable** → local copy on Hetzner is the failover (14-day window).
- **`.env` lost (encryption key gone)** → backups become unrestorable. Keep `.env` in two places off-server (your laptop + a secure cloud note manager).

## Old (deprecated)

`pg_dump_hourly.sh`, `pg_basebackup_nightly.sh`, `b2_upload.sh`, `recovery_rehearsal.sh` reference `/home/ubuntu/trading-radar` — that path was the spec's target but never matched the actual deployment at `/opt/trading-radar` on Hetzner. They have been removed; the schedule now lives in `scripts/backup.sh`.
