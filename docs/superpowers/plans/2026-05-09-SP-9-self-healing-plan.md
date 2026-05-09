# SP-9 Self-Healing Watchdog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** When the operator can't be available to debug, the bot detects common failure modes + recovers autonomously. For things it can't fix, it surfaces them via Telegram so the operator can act.

**Realistic scope:** known-failure self-healing (~80% of incidents are predictable). Unknown failure modes get loud alerts; an LLM-diagnosis hook is left as an opt-in (costs API tokens).

---

## What CAN be self-healed (no human, no AI)

| Failure | Detection | Auto-fix |
|---|---|---|
| Backend container crash-loop | `docker inspect tr-backend --format {{.RestartCount}}` > 5 in 24h | Restart Docker, alert if persists |
| OOM / memory leak | `free -h` shows > 90% used | `docker compose restart backend` (containers reset memory) |
| Disk > 80% | `df -h /var/lib/docker /var/backups` | Prune backups older than retention; vacuum docker images |
| Disk > 95% | same | Stop training jobs (free disk); critical alert |
| Stale predictions (no writes for 2h) | `SELECT count(*) FROM predictions WHERE ts > NOW() - INTERVAL '2 hours'` = 0 | Restart backend (live worker stuck) |
| Binance API outage | `/fapi/v1/ping` fails | Already handled by kill switches → freeze |
| Backup not run in 25h | Most recent `backup_*.sql.gz.enc` mtime > 25h | Run manual backup now; alert |
| Audit chain break | SP-7 verifier (already exists) | Auto-demote to Manual + freeze (already wired) |
| Cron job didn't fire | `tail /var/log/trading-radar-*.log` shows no recent run | Restart cron daemon; re-run job |

## What CANNOT be self-healed without human/AI

- New code bugs (e.g. recent deploy regressed something)
- Strategy underperformance (model drift, regime change)
- Binance API changes (new field shape)
- Logic errors in scoring / sizing

For these: **loud Telegram alert** + log dump. Operator decides.

## Optional: LLM-assisted log diagnosis (separate PR)

Daily cron: tail `/var/log/trading-radar-backend.log` for last 24h, send WARNING+ERROR lines to Claude API, get back a one-line summary + suggested action. Telegram-post the summary.

Cost: ~$0.01–$0.05/day depending on log volume. Off by default; opt in with `LLM_DIAGNOSIS_ENABLED=true` + `ANTHROPIC_API_KEY=...`.

---

## Phase A — Watchdog cron + Telegram alerts

**Files:**
- Create: `scripts/watchdog.sh` — bash cron that runs every 15 min on Hetzner
- Create: `scripts/install_watchdog_cron.sh` — one-time installer (mirror of backup installer)
- Test: `scripts/test_watchdog.sh` — manual smoke

`watchdog.sh` runs each check; on failure, takes the auto-fix action; sends a Telegram message describing what it found + did. Idempotent — safe to run every 15 min.

- [ ] Step A1: write watchdog.sh with all the checks above
- [ ] Step A2: write install_watchdog_cron.sh (idempotent installer)
- [ ] Step A3: write a brief README + add to operator guide
- [ ] Step A4: deploy to Hetzner; verify first run sends a "all OK" Telegram

## Phase B — Restart-on-stuck-prediction-worker

**Files:**
- Modify: `backend/app/ws/live_prediction.py` — add a heartbeat that updates a key in Redis every 60s
- Modify: `scripts/watchdog.sh` — check Redis key age > 5min → docker compose restart backend

Heartbeat lets watchdog distinguish between "live worker running fine, just no new candle yet" vs "live worker stuck in WS reconnect loop and needs restart".

## Phase C (optional, opt-in) — LLM diagnosis

**Files:**
- Create: `scripts/llm_diagnose.sh` — daily cron at 04:00 UTC
- Create: `tools/llm_diagnose.py` — reads logs, calls Claude API, posts summary

Costs token money. Off by default. Documented in operator guide as "if you want a daily AI-generated incident report, set ANTHROPIC_API_KEY".

---

## Acceptance criteria

- [ ] `bash scripts/watchdog.sh --dry-run` shows what each check would report without taking action
- [ ] First production run sends a baseline "all OK / X warnings" Telegram message
- [ ] Disk-full simulation (`fallocate -l 5G /var/backups/test.bin`) → watchdog prunes + alerts
- [ ] Backend kill (`docker kill tr-backend`) → watchdog detects on next tick + restarts container
- [ ] No false positives in 7 days of normal operation
