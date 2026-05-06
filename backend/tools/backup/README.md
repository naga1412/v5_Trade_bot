# Backup & Recovery (SP-7 Phase E)

Operator runbook for the `tools/backup/` pipeline. Covers the backup tools
implemented in SP-7 Phase E: nightly `pg_basebackup`, encrypted upload to
Backblaze B2, rsync to the operator's laptop, and the quarterly recovery
rehearsal.

These Python tools supersede the SP-0 bash scripts in `infra/backup/`. Every
backup operation persists a row to the `backup_runs` table (migration 0012)
with type / target / success / size / duration / error fields.

## Module map

| Module | Purpose | Trigger |
|---|---|---|
| `snapshot.py` | `pg_basebackup -D <out_dir> -Ft -z` wrapper | nightly cron |
| `upload_b2.py` | AES-256-GCM encrypt + boto3 upload to B2 | nightly cron, after snapshot |
| `rsync_laptop.py` | `rsync -avz --partial` snapshot to laptop SSD over SSH | nightly cron, after snapshot |
| `recovery_rehearsal.py` | Pull latest B2 backup, decrypt, restore to throwaway DB, compare row counts | quarterly cron / manual |
| `_persistence.py` | `record_backup_run(...)` -> INSERT into `backup_runs` | called by every CLI |

## Required environment variables

| Variable | Purpose | Default | Required for |
|---|---|---|---|
| `BACKUP_PGHOST` | Postgres host | `postgres` | snapshot, recovery, persistence |
| `BACKUP_PGPORT` | Postgres port | `5432` | snapshot, recovery, persistence |
| `BACKUP_PGUSER` | Postgres user | `postgres` | snapshot, recovery, persistence |
| `BACKUP_PGPASSWORD` | Postgres password | `''` (empty) | snapshot, recovery, persistence |
| `POSTGRES_DB` | Production DB name (used for prod row-count) | `trading_radar` | recovery, persistence |
| `BACKUP_ENCRYPTION_KEY` | AES-256-GCM key (base64-encoded 32 bytes) | _(unset)_ | upload_b2, recovery |
| `B2_BUCKET` | Backblaze B2 bucket name | _(unset)_ | upload_b2, recovery |
| `B2_S3_ENDPOINT` | B2 S3-compatible endpoint URL | `https://s3.us-west-002.backblazeb2.com` | upload_b2, recovery |
| `LAPTOP_RSYNC_TARGET` | Rsync destination, e.g. `user@laptop.lan:/mnt/ext/backups/` | _(unset, skips rsync)_ | rsync_laptop |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | B2 application key (S3-compat creds) | _(unset)_ | upload_b2, recovery |

Generate the encryption key (one-time, store in the secrets vault):

```bash
python -c "import os, base64; print(base64.b64encode(os.urandom(32)).decode())"
```

**WARNING:** if you lose `BACKUP_ENCRYPTION_KEY`, every encrypted blob in B2
becomes unrecoverable. Back up the key out-of-band (1Password / printed safe
copy). The key is system-wide; rotation requires re-encrypting all retained
blobs (procedure not yet automated).

## Cron schedule (Oracle host)

Add to `/etc/cron.d/trading-radar-backups`:

```cron
# Hourly: pg_dump of changed tables (data only, gz) -- kept 7 days locally.
# (Hourly dump remains in infra/backup/pg_dump_hourly.sh; the python tools
#  in this directory cover nightly + quarterly. Hourly is the SP-7 RPO=1h
#  primary copy; nightly basebackup is the disaster-recovery floor.)
0 * * * * trading-radar /home/ubuntu/trading-radar/infra/backup/pg_dump_hourly.sh

# Nightly 00:30 UTC: full pg_basebackup -> encrypt -> upload to B2 -> rsync to laptop
30 0 * * * trading-radar /home/ubuntu/trading-radar/infra/backup/nightly.sh

# Quarterly (1st of Jan/Apr/Jul/Oct, 12:00 UTC): recovery rehearsal
0 12 1 1,4,7,10 * trading-radar /home/ubuntu/trading-radar/infra/backup/quarterly_rehearsal.sh
```

Where the wrapper scripts invoke the Python CLIs:

```bash
# /home/ubuntu/trading-radar/infra/backup/nightly.sh
#!/usr/bin/env bash
set -euo pipefail
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT=/var/backups/trading-radar/full_${TS}
docker compose -f /home/ubuntu/trading-radar/docker-compose.yml \
    exec -T backend python -m tools.backup.snapshot --out "${OUT}"
docker compose -f /home/ubuntu/trading-radar/docker-compose.yml \
    exec -T backend python -m tools.backup.upload_b2 --snapshot-dir "${OUT}"
docker compose -f /home/ubuntu/trading-radar/docker-compose.yml \
    exec -T backend python -m tools.backup.rsync_laptop --snapshot-path "${OUT}"
# Retention: keep last 7 daily snapshots locally
find /var/backups/trading-radar -maxdepth 1 -type d -name 'full_*' -mtime +7 -exec rm -rf {} +
```

```bash
# /home/ubuntu/trading-radar/infra/backup/quarterly_rehearsal.sh
#!/usr/bin/env bash
set -euo pipefail
WORK=/tmp/recovery_$(date -u +%Y%m%dT%H%M%SZ)
docker compose -f /home/ubuntu/trading-radar/docker-compose.yml \
    exec -T backend python -m tools.backup.recovery_rehearsal --work-dir "${WORK}"
rm -rf "${WORK}"
```

## Manual operations

### Take a snapshot now

```bash
docker compose exec backend python -m tools.backup.snapshot \
    --out /var/backups/trading-radar/full_manual_$(date -u +%s)
```

### Upload an existing snapshot to B2

```bash
docker compose exec backend python -m tools.backup.upload_b2 \
    --snapshot-dir /var/backups/trading-radar/full_<ts>
```

### Run the recovery rehearsal manually

```bash
docker compose exec backend python -m tools.backup.recovery_rehearsal \
    --work-dir /tmp/rehearsal_$(date -u +%s)
```

The script exits with code 0 on success (all chained tables within ±1 row of
production) and 1 on failure. The result is also persisted to `backup_runs`
with `backup_type='recovery_rehearsal'`.

## Backup destinations

| Destination | Purpose | Retention | Notes |
|---|---|---|---|
| Oracle local FS (`/var/backups/trading-radar/`) | Hot recovery, hourly + nightly | 7 days | Same host as primary DB; first thing to disappear in a host failure |
| Backblaze B2 (`s3://${B2_BUCKET}/db-snapshots/`) | Off-site disaster recovery | 30 days (lifecycle policy on bucket) | Encrypted with `BACKUP_ENCRYPTION_KEY` before upload |
| Operator laptop SSD (rsync target) | Off-site cold copy | Manual / unbounded | Per spec §2.7 — last-resort restore source if B2 + Oracle are both lost |

## Restore procedure (disaster recovery)

If the Oracle host is destroyed and B2 is the only remaining copy:

1. Spin up a fresh Postgres 16 host.
2. Find the latest blob:

   ```bash
   aws s3 ls "s3://${B2_BUCKET}/db-snapshots/" --recursive \
       --endpoint-url "${B2_S3_ENDPOINT}" | sort | tail -5
   ```

3. Download + decrypt:

   ```bash
   aws s3 cp "s3://${B2_BUCKET}/db-snapshots/<date>/<file>.tar.gz.enc" \
       /tmp/snapshot.tar.gz.enc --endpoint-url "${B2_S3_ENDPOINT}"
   python -c "
   import base64, os
   from tools.backup.upload_b2 import decrypt_bytes_aes_gcm
   blob = open('/tmp/snapshot.tar.gz.enc', 'rb').read()
   key = base64.b64decode(os.environ['BACKUP_ENCRYPTION_KEY'])
   open('/tmp/snapshot.tar.gz', 'wb').write(decrypt_bytes_aes_gcm(blob, key=key))
   "
   ```

4. Extract the tarball into the new Postgres data directory:

   ```bash
   systemctl stop postgresql
   tar -xzf /tmp/snapshot.tar.gz -C /var/lib/postgresql/16/main/
   chown -R postgres:postgres /var/lib/postgresql/16/main/
   systemctl start postgresql
   ```

5. Run `alembic upgrade head` to ensure schema is current (in case the
   snapshot predates a schema migration that ran post-snapshot).
6. Run `python -m tools.backup.recovery_rehearsal --work-dir /tmp/verify`
   pointed at the new instance to confirm the restore is complete.

If only the laptop copy survives, use `rsync` to push the snapshot directory
back to the new host and follow steps 4-6.

## RPO / RTO commitments (per spec §2.7, §5.13)

| Metric | Commitment | How achieved |
|---|---|---|
| **RPO** (recovery point objective) | 1 hour | Hourly `pg_dump` script (`infra/backup/pg_dump_hourly.sh`) |
| **RTO** (recovery time objective) | 4 hours | Documented restore procedure above; rehearsed quarterly |
| **Off-site copies** | 2 (B2 + laptop) | nightly upload_b2 + nightly rsync_laptop |
| **Encryption** | All off-site copies AES-256-GCM | `upload_b2.py` always encrypts before upload |

## Test environment notes

- `pg_basebackup` is **not** in the backend container's PATH (it ships in the
  postgres image only). Unit tests therefore mock `subprocess.run` and never
  exercise the real binary. In production, the wrapper scripts above invoke
  the CLI inside the backend container, which then shells out to a host that
  has `postgresql-client` installed -- OR the operator runs
  `docker compose exec postgres pg_basebackup ...` directly (then uses the
  Python upload + rsync tools afterwards).
- `psycopg2` is a transitive Alembic dep, but it isn't installed by default
  in every dev environment. The integration test
  (`tests/integration/test_backup_runs_persisted.py`) `pytest.skip`s when
  psycopg2 or Postgres are unavailable.

## Querying backup history

```sql
-- last 24 hours of backup activity
SELECT started_at, backup_type, target, success, size_bytes, duration_seconds, error_message
FROM backup_runs
WHERE started_at > now() - interval '1 day'
ORDER BY started_at DESC;

-- success rate by backup_type, last 30 days
SELECT backup_type,
       count(*) FILTER (WHERE success) AS ok,
       count(*) FILTER (WHERE NOT success) AS failed,
       round(100.0 * count(*) FILTER (WHERE success) / count(*), 1) AS success_pct
FROM backup_runs
WHERE started_at > now() - interval '30 days'
GROUP BY backup_type
ORDER BY backup_type;

-- last successful nightly basebackup
SELECT * FROM backup_runs
WHERE backup_type = 'nightly_basebackup' AND success
ORDER BY started_at DESC LIMIT 1;
```
