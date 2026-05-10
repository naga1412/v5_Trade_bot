# Staging Environment

Same Hetzner box as production, but a fully isolated stack — separate
container names, ports, volumes, Postgres database, and config.
Auto-deployed on every push to the `dev` branch.

## Architecture

```
                     Hetzner CX22 box
                  ┌──────────────────────────────┐
  prod traffic ───┤ docker-compose.yml           │
  aji12.nag…com   │   tr-backend       :8000     │
  via CF tunnel   │   tr-postgres      :5432     │
                  │   tr-frontend      :5173     │
                  │   trading_radar DB           │
                  └──────────────────────────────┘
                  ┌──────────────────────────────┐
  staging traffic ┤ docker-compose.staging.yml   │
  staging.aji12…  │   tr-staging-backend  :8001  │
  via CF tunnel   │   tr-staging-postgres :5433  │
                  │   tr-staging-frontend :5174  │
                  │   trading_radar_staging DB   │
                  └──────────────────────────────┘
```

## Branch flow

```
feature/foo  ──merge──▶  dev  ──merge──▶  main
                          │                 │
                          ▼                 ▼
                       staging            production
```

| Branch | Auto-deploys to | Hostname | Purpose |
|---|---|---|---|
| `feature/*` | nothing (CI only) | none | I write changes here |
| `dev` | staging | `staging.aji12.nagayuaj.com` | I drive Playwright + you click around |
| `main` | production | `aji12.nagayuaj.com` | real-money trading |

## One-time setup

### 1. Cloudflare Tunnel — add a staging hostname

Open the Cloudflare Zero Trust dashboard → Networks → Tunnels → your
existing trading-radar tunnel → **Public Hostname** tab → **Add a
public hostname**:

| Field | Value |
|---|---|
| Subdomain | `staging` |
| Domain | `aji12.nagayuaj.com` |
| Service Type | `HTTP` |
| URL | `localhost:5174` |

Save. Then under **Access** → create a new Access application for
`staging.aji12.nagayuaj.com` with whatever auth policy you want
(easiest: same email-based policy as prod).

### 2. Branch protection on `main`

GitHub → repo Settings → Branches → Add branch protection rule:

| Field | Value |
|---|---|
| Branch name pattern | `main` |
| Require pull request reviews before merging | ✅ |
| Required approvals | 1 (you) |
| Require status checks to pass before merging | ✅ |
| Required checks | `backend`, `frontend`, `docker-compose-smoke`, `playwright-e2e` |
| Require branches to be up to date | ✅ |
| Restrict pushes that create matching branches | ✅ |

Now `main` can ONLY receive commits via reviewed PRs from `dev` (or
hotfix branches you explicitly approve).

### 3. Create the `dev` branch

```powershell
git checkout main
git pull
git checkout -b dev
git push -u origin dev
```

This first push to `dev` triggers the staging deploy. The deploy
workflow auto-clones the repo into `/opt/trading-radar-staging` on
first run, copies `.env.staging.example` to `.env.staging`, and then
expects you to edit `.env.staging` with real CF Access values + a
unique passphrase before re-running.

### 4. Verify staging is up

```powershell
ssh -i $HOME\.ssh\oracle_key root@95.216.187.204 \
  "cd /opt/trading-radar-staging && \
   docker compose -f docker-compose.staging.yml --env-file .env.staging ps"
```

Then open `https://staging.aji12.nagayuaj.com` in your browser. You
should see the dashboard with empty data (fresh staging DB).

## Daily flow

```
1. I create feature/<thing> off dev
2. I push commits, open a PR -> dev
3. CI runs: backend pytest + frontend vitest + Playwright e2e
4. PR auto-merges to dev when green
5. Deploy workflow auto-deploys dev branch -> staging
6. I drive Playwright against staging.aji12.nagayuaj.com to verify
7. You click around staging to confirm UX
8. When happy, I open a "promote dev to main" PR
9. You review + approve
10. PR merges -> main -> auto-deploys to production
```

## Resource sharing notes

Staging shares the Hetzner box with prod. Concurrent resource use:

- **CPU**: prod backend usually idles between candles; staging mostly idle. Brief spikes during builds (~30s).
- **RAM**: prod ~1.5 GB, staging ~1 GB, postgres pair ~1 GB combined, OS ~500 MB → headroom OK on 8 GB CX22.
- **Disk**: prod backups 270 KB/day; staging volumes will grow over time. Monitor with `df -h`. Run `docker system prune -a` monthly.
- **Network**: separate Cloudflare tunnels but same public IP — Binance API rate limits are shared. Staging mostly fetches the same symbols, so cache benefits both.

If staging starts hurting prod, the cleanest exit ramp is to spin a
separate Hetzner CX22 ($5/mo) and move staging there — same compose
file, just different host.
