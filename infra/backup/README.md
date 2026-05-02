# Backup & Recovery (SP-0)

## Schedule
- Hourly: `tr_pg_dump_hourly.sh` — data-only dump → /var/backups/trading-radar/hourly_*.sql.gz (72h retention)
- Nightly 02:30 UTC: `tr_pg_basebackup_nightly.sh` — full base + B2 + laptop rsync (7-day retention)

## RPO / RTO
- RPO: 1 hour (worst case = data lost since last hourly dump)
- RTO: 4 hours (full restore + redeploy stack)

## Cloud → laptop sync
Set `LAPTOP_RSYNC_TARGET=user@laptop.lan:/mnt/external_ssd/trading-radar-backups/` in `/home/ubuntu/trading-radar/.env`.
Laptop must have SSH server running with key-based auth from Oracle.

## Recovery rehearsal (quarterly)
Run on laptop:
```bash
infra/backup/recovery_rehearsal.sh
```
Then manually compare reported row counts to Oracle production using the printed command. Archive the output in `docs/superpowers/log.md`.

## Failure-mode plan
- **Oracle suspended:** restore from latest B2 to laptop dev stack → flip Cloudflare Tunnel target → run from laptop until new Oracle account.
- **B2 unavailable:** laptop SSD copy is the failover.
- **Both unavailable + Oracle running:** Oracle host is the source of truth; rebuild backups going forward.
