# SP-8 Autonomous Trading — Operator Setup Guide

After PR #65 (Phase I) lands, the entire SP-8 codebase is in main:
schema, modes, gates, leverage math, kill switches, vault, TOTP, Binance
live client, Telegram per-trade approve, tax events, FIFO, ITR-3 export,
UI tab, and pre-flight checks.

The autonomous-trading subsystem stays **OFF** until the operator
explicitly enables it. This guide walks the one-time setup.

---

## Pre-conditions (do these once)

### 1. Generate Binance Futures API key

On Binance:
1. Account → API Management → Create API
2. Label: `trading-radar-prod` (or `trading-radar-testnet` for testnet)
3. Permissions:
   - ✅ Enable Reading
   - ✅ Enable Futures
   - ❌ **Enable Withdrawals** — leave OFF (spec §9.3 hard rule)
   - ❌ **Enable Internal Transfer** — leave OFF
   - ❌ **Permits Universal Transfer** — leave OFF
4. IP restriction: enable + whitelist Hetzner public IP only (`95.216.187.204`)
5. Save the API key + secret somewhere temporary (your password manager)

For first-time setup, use a **testnet** key from
<https://testnet.binancefuture.com>. Live keys come later.

### 2. Encrypt the keys into the vault

On your laptop (NOT on the server — plaintext keys must never touch a remote disk):

```bash
cd /path/to/v5_Trade_bot
py -3.11 tools/secrets/encrypt.py --out secrets.enc
```

Prompts:
- `binance_api_key`: paste API key
- `binance_api_secret`: paste API secret
- `telegram_bot_token`: from @BotFather (skip if not using Telegram approve mode)
- `telegram_chat_id`: from @userinfobot (skip if not using Telegram)
- `Passphrase`: ≥ 16 chars; mix case + special chars. **Store in your password manager AND your head — there is no recovery.**

The script writes `secrets.enc` (binary, encrypted). Safe to commit to git.

### 3. Set up TOTP for hardware-confirm

In the running app's UI (Settings → Autonomous → TOTP setup):
1. Backend generates a base32 secret + provisioning URI
2. Scan the QR with Authy / Google Authenticator
3. Save the 10 backup codes in your password manager
4. Verify with one TOTP code from the app

(Phase J wires this UI flow; until then it's: `tools/secrets/totp_setup.py`
which prints the URI for manual scan.)

### 4. Deploy

```bash
# On Hetzner
cd /opt/trading-radar
git pull origin main
```

Add to `/opt/trading-radar/.env`:

```
MASTER_PASSPHRASE=<your-passphrase-from-step-2>
AUTONOMOUS_TRADING_ENABLED=true
```

(Set `BINANCE_USE_TESTNET=false` only after weeks of successful testnet
operation. **Default is testnet.**)

```bash
# Copy secrets.enc into place
scp secrets.enc root@95.216.187.204:/opt/trading-radar/backend/secrets.enc
ssh root@95.216.187.204 "cd /opt/trading-radar && docker compose up -d --build backend"
```

### 5. Verify pre-flight

```bash
ssh root@95.216.187.204 \
  "docker compose -f /opt/trading-radar/docker-compose.yml logs --tail=50 backend | grep -i preflight"
```

Expected:
```
preflight: 5/5 checks passed
```

If any check fails, the autonomous workers do NOT start; the rest of
the platform (paper trading, ghost candles, dashboard) keeps running
normally. Fix the failing check + restart.

---

## After pre-flight passes

The Autonomous tab in the dashboard (https://aji12.nagayuaj.com → Autonomous)
shows your current mode (default: Manual) and the promotion-gate status.

### Promotion path

| To unlock | Requires (rolling 30 days for Telegram, 90 for Fully-auto) |
|---|---|
| **Telegram-approve** | ≥30 days continuous, ≥100 closed trades, Sharpe ≥1.0, MaxDD ≤12%, win rate ≥40%, profit factor ≥1.5 |
| **Fully-auto** | ≥90 days continuous, ≥300 closed trades, Sharpe ≥1.5, MaxDD ≤10%, win rate ≥45%, profit factor ≥2.0 |

Stats are computed from closed `shadow_trades` + `live_trades` over the
rolling window.

In short: **the system requires months of paper-trade history before
any real money flows.** Once gates pass, mode upgrades require hardware-
confirm (TOTP code) — UNLESS auto-promote is enabled (next section).

### Unattended auto-promotion (optional)

If you can't be available to manually flip modes (Claude subscription
ended, away from computer for months, etc.), enable the daily 03:30 UTC
auto-promote worker:

```
# /opt/trading-radar/.env
AUTO_PROMOTE_TO_TELEGRAM_ENABLED=true     # Manual → Telegram-approve
AUTO_PROMOTE_TO_FULLYAUTO_ENABLED=true    # Telegram-approve → Fully-auto
AUTO_PROMOTE_CONSECUTIVE_DAYS=7           # gates must pass for N days running
```

Behavior:
- Worker runs once daily at 03:30 UTC (after the brain-retrain cron)
- For each user whose mode could be auto-promoted, computes the spec §4
  gates over the past N days; if **every** day passes, the worker
  upgrades the mode without hardware-confirm
- Audit log records `triggered_by='auto-demote'` (closest existing enum)
  + the gate snapshot that justified the upgrade
- Hardware-confirm is bypassed; **kill switches still apply**
  post-promotion (auto-demote on daily loss > 5% etc.)

Disarm at any time:
- Set the env var to false + `docker compose restart backend`
- Or send `/freeze` via Telegram (halts all autonomous trading)
- Or downgrade the mode manually via the UI (downgrades always allowed)

Default is OFF — auto-promotion only fires when the env vars are
explicitly set.

---

## Self-healing watchdog

Spec: [SP-9 plan](superpowers/plans/2026-05-09-SP-9-self-healing-plan.md).

A cron-driven watchdog (`/opt/trading-radar/scripts/watchdog.sh`) runs
every 15 minutes and auto-recovers from common failure modes:

| Failure | Auto-action |
|---|---|
| Backend container missing | `docker compose up -d backend` |
| Backend in restart loop | Telegram alert (operator must investigate) |
| Memory > 90% | Restart backend (resets memory leaks) |
| Docker disk > 80% | `docker system prune -f`; reduce backup retention to 14 days |
| Docker disk > 95% | Aggressive prune; reduce retention to 7 days; emergency alert |
| Postgres not ready | Restart postgres |
| No predictions in 2h | Restart backend (live worker stuck) |
| Backup not run in 25h | Run `backup.sh` manually |
| Binance Futures unreachable | Telegram alert (kill switches in backend handle the freeze) |

Install once:

```bash
ssh root@95.216.187.204
cd /opt/trading-radar
sudo ./scripts/install_watchdog_cron.sh
```

Verify:

```bash
crontab -l | grep watchdog
# */15 * * * * /opt/trading-radar/scripts/watchdog.sh >> /var/log/trading-radar-watchdog.log 2>&1
```

The watchdog sends a Telegram message ONLY when it detects a problem
(or takes auto-action). Quiet runs are silent — `tail -f
/var/log/trading-radar-watchdog.log` shows the heartbeat.

### What the watchdog can NOT fix

New code bugs, strategy underperformance, Binance API schema changes.
For those: loud Telegram alert (so you know to investigate) but no
auto-fix. Roll back the bad commit + redeploy:

```bash
ssh root@95.216.187.204
cd /opt/trading-radar
git log --oneline -5         # find the last good commit
git reset --hard <good-sha>
docker compose up -d --build backend
```

### Optional: LLM-assisted log diagnosis

For an opt-in daily AI-generated incident summary, set:

```
ANTHROPIC_API_KEY=sk-ant-...
LLM_DIAGNOSIS_ENABLED=true
```

Cost: ~\$0.01–\$0.05/day depending on log volume. Off by default —
needs your own Anthropic API key + token spend.

### Auto-demotion (spec §4.4)

The system automatically downgrades modes if:
- Daily loss > 5% portfolio → Fully-auto demotes to Telegram-approve
- 10 consecutive losses → Fully-auto demotes to Telegram-approve
- Gates fall below threshold for 7 consecutive days → Telegram-approve demotes to Manual
- Audit chain integrity violation → any mode → Manual + freeze

You get a Telegram alert + email on every auto-demotion.

### Emergency commands (Telegram bot)

| Command | Effect |
|---|---|
| `/freeze` | Stop all autonomous trading immediately. Open positions stay open. |
| `/unfreeze` | Resume autonomous trading. |
| `/closeall` | Cancel all open positions at market price NOW. |
| `/status` | Reply with current open positions + 24h P&L + bot mode. |
| `/kill` | Hard stop: freeze + closeall + downgrade to Manual. |

All commands require message sender = configured `telegram_chat_id`.

---

## Tax compliance (India / ITR-3)

Every closed live_trade auto-generates a `tax_events` row:
- 1% TDS on exit value (Section 194S)
- FIFO cost basis matched against prior buys
- INR-converted at trade-close USD/INR rate
- Stored hash-chained for audit

At year-end (1 April – 31 March FY): Settings → Autonomous → Tax Export
produces a Schedule VDA CSV compatible with ClearTax / Quicko.

Monthly TDS reminder fires at 05:00 IST on the 1st with the prior
month's total + deposit deadline (7th of current month).

---

## Rotating the Binance key (every 90 days, recommended)

1. Generate new key with the same restricted permissions
2. On laptop: `py -3.11 tools/secrets/encrypt.py` with new keys + same passphrase
3. Commit + push the updated `secrets.enc`
4. SSH: `cd /opt/trading-radar && git pull && docker compose restart backend`
5. Old key on Binance: Account → API Management → Delete

The bot alerts at day 80 of the cycle: "Binance keys are 80 days old.
Rotate within 10 days."

---

## Failure-mode handbook

| Scenario | Recovery |
|---|---|
| Backend crashed (any reason) | Containers auto-restart. Live worker resumes from last heartbeat. Open positions are NOT cancelled (Binance keeps them). |
| Hetzner box dies | Restore from backup (`scripts/restore_backup.sh`), redeploy `docker compose up -d`, flip Cloudflare Tunnel to new box. RTO: ~2 hours. |
| Binance API down | Pre-flight passes (uses cached liveness); live trades fail → kill_switches.network_outage trips after 60s → freeze. Resumes when Binance recovers. |
| MASTER_PASSPHRASE forgotten | Vault is unrecoverable. Create new keys, re-run `tools/secrets/encrypt.py` with a NEW passphrase. Lose the encrypted historical secrets. |
| Audit chain break detected | All trading auto-demotes to Manual + freeze. Investigate via `tools/audit/verify_chain.py`. |

---

## Spec references

- Master spec: [autonomous-trading-design.md](superpowers/specs/2026-05-03-autonomous-trading-design.md)
- Plan: [SP-8 plan](superpowers/plans/2026-05-09-SP-8-autonomous-trading-plan.md)
- Phase commits: PR #55 (A), #56 (C), #57 (universe), #58 (A hotfix), #59 (E),
  #60 (B), #61 (D), #62 (H), #63 (F), #64 (G), #65 (I), #66 (J pre-flight)
