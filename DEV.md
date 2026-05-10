# Local Dev Environment

A fully isolated copy of trading-radar that runs on your laptop with
its own database. Use this for everything except real-money trading.

## One-time setup

```powershell
# In repo root
copy .env.dev.example .env.dev
docker compose --env-file .env.dev up -d --build
```

Wait ~60 seconds for the backend to migrate + the frontend to compile.

Then open **http://localhost:5173** in your browser. You should see
the trading-radar dashboard with the Live Prediction tab loaded.

## Daily use

```powershell
# Start
docker compose --env-file .env.dev up -d

# View logs (Ctrl+C to stop following — containers keep running)
docker compose --env-file .env.dev logs -f backend

# Stop (containers paused, data preserved)
docker compose --env-file .env.dev down

# Stop + wipe ALL local data (start fresh)
docker compose --env-file .env.dev down -v
```

## What's different from production

| Setting | Prod | Dev |
|---|---|---|
| `ENV` | `production` | `development` |
| Cloudflare Access | required (JWT verified) | bypassed (LAN) |
| `BINANCE_USE_TESTNET` | `true` (default) | `true` (always) |
| `AUTONOMOUS_TRADING_ENABLED` | `true` | `false` (paper only) |
| Database | persistent Hetzner volume | docker-volume on your laptop |
| Backups | daily 03:15 UTC cron | none |
| Watchdog | 15-min cron | none |

## Recommended workflow

1. Branch off `main`: `git checkout -b feat-something`
2. Make code changes
3. Hot-reload picks them up automatically (Vite watches frontend, backend reloads on save)
4. Test in your browser at http://localhost:5173
5. When happy, commit + push the branch
6. Open a PR — CI runs all tests + Playwright e2e (see PR gate below)
7. Merge to main when CI is green
8. The Hetzner production deploy fires automatically

## Loading real predictions data into dev

The dev DB starts empty. To get real predictions for chart testing,
either:

- **Wait** — the dev backend's live-prediction worker generates one
  prediction per closed candle (1h timeframe by default). Open the
  dashboard, leave it running, and predictions accumulate.
- **Or seed** with a snapshot of prod data via:
  ```bash
  ssh root@<hetzner> "cat /var/backups/trading-radar/backup_$(date +%Y-%m-%d).sql.gz.enc" \
    > local-snapshot.sql.gz.enc
  # Decrypt with the prod passphrase, gunzip, restore to dev postgres:
  openssl enc -d -aes-256-cbc -in local-snapshot.sql.gz.enc -out local-snapshot.sql.gz \
    -pass pass:"$PROD_BACKUP_PASSPHRASE"
  gunzip local-snapshot.sql.gz
  docker compose --env-file .env.dev exec -T postgres \
    psql -U postgres -d trading_radar_dev < local-snapshot.sql
  ```

## Troubleshooting

- **`http://localhost:5173` shows "Connection refused"**
  Containers not started yet. Wait 60s after `docker compose up`.

- **Backend logs show `DATABASE_URL not set`**
  You forgot `--env-file .env.dev`. Always pass it explicitly.

- **Backend says `pre-flight failed`**
  Expected in dev — `secrets.enc` doesn't exist by default. Autonomous
  trading stays OFF, paper mode keeps working. Ignore.

- **`docker compose` not found**
  Install Docker Desktop for Windows + enable WSL2 integration.

- **Port 5432 / 6379 / 8000 / 5173 already in use**
  Either stop the conflicting service or override the port in
  `docker-compose.override.yml`.

## DO NOT

- Don't point dev at the prod Postgres. Dev Postgres is a separate
  container with its own volume. Reading from prod from dev is fine
  via the snapshot flow above; writing is what we want to avoid.
- Don't put real Binance keys in `.env.dev`. Put them in a
  `secrets.enc` if you need to test the vault flow, but keep
  `BINANCE_USE_TESTNET=true` so even with leaked keys the worst case
  is testnet money.
