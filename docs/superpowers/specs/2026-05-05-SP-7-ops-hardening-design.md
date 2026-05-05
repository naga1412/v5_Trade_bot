# SP-7 — Ops Hardening Design Spec

**Date:** 2026-05-05
**Status:** Approved (autonomous-mode default; user can redirect)
**Implementation target:** Sub-project SP-7 (after SP-6 ship)
**Depends on:** SP-2 (158 patterns), SP-3 (4 adapters), SP-5 (10 layers + traps), SP-6 (UI)
**Companion specs:** `2026-05-01-trading-radar-meta-plan-design.md` §2.7 (backups), §3 §181 (acceptance)

---

## 1. Purpose

Harden the operational surface of trading-radar: **backtest framework** + **hyperopt for layer weights**, **audit chain verifier** (nightly), **DR backup pipeline** (B2 wire-up — gap from SP-1), **monitoring stack** (Prometheus + Grafana + Loki), **latency instrumentation** (p99 < 500ms target), and **champion-challenger gate** for ML checkpoint auto-promotion.

After SP-7 ships, trading-radar is production-grade: every prediction's audit chain is verified nightly, backups are guaranteed restorable, model checkpoints are auto-promoted only if they beat the current champion on a held-out backtest, and operators have visibility into latency + error budgets via Grafana.

### Non-goals

- **No autonomous trading.** SP-8 territory; SP-7 ships only the analysis + ops infrastructure.
- **No new scoring layers or patterns.** SP-7 is pure ops + tooling.
- **No frontend changes** beyond a new `Admin → Monitoring` sub-page that links to Grafana (defer the embed/iframe to SP-7.5 if Grafana auth is fiddly).
- **No new exchange adapters.** SP-3.5 territory.
- **No paid monitoring services** (no Datadog, no Sentry paid tier — local Prometheus/Grafana only).

---

## 2. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | Backtest framework | **Freqtrade as library** per meta-plan §2.1 — imported in `tools/backtest.py`; no docker service |
| 2 | Hyperopt search | **Bayesian** via `optuna==4.0.0` (smaller than Freqtrade's hyperopt; flexible search space) |
| 3 | Hyperopt target | **Layer weights** (currently equal 1/9 across L1-L9 from meta-plan §2.3); search for weights that maximize backtest Sharpe |
| 4 | Audit chain verifier | New module `app/db/audit_verify.py` extended (already exists from SP-0); nightly cron runs `verify_chain(table)` for `predictions`, `paper_trades`, `shadow_trades`, `tax_events` (when SP-8 adds it), alerts admin on any failure |
| 5 | Verifier cadence | Nightly at 03:00 UTC (between universe sync 00:00 + adapter universe sync 02:00 + brain training 04:00) |
| 6 | DR backup pipeline | New module `tools/backup/` — pg_basebackup → encrypted B2 upload + laptop rsync per meta-plan §2.7 line 117 |
| 7 | Backup cadence | **Hourly** pg_dump of changed tables → Oracle local disk; **nightly** pg_basebackup → B2 + laptop |
| 8 | Backup retention | B2: 30 days rolling. Laptop: 90 days. Oracle local: 7 days. |
| 9 | Recovery rehearsal | **Quarterly** automated script that pulls latest B2 backup → restores to a fresh Postgres container → asserts row counts match production |
| 10 | Monitoring stack | **Prometheus + Grafana + Loki + Promtail** as docker services per meta-plan §8 (already in spec, not yet wired) |
| 11 | Latency target | **p99 < 500ms** for all REST endpoints; instrumented via `prometheus-fastapi-instrumentator` |
| 12 | Champion-challenger gate | New `app/ml/champion_challenger.py` module — when admin activates a new ML checkpoint, run held-out backtest; only flip `is_active=true` if new MAE < current MAE × 0.95 (5% improvement bar) |
| 13 | Alerting | **Email-only** for v1 (via SMTP through cloudflared tunnel OR direct SMTP). Slack/Telegram defer to user preference. |
| 14 | Coverage gate in CI | Backend ≥85% on critical modules (per CLAUDE.md); enforce via `pytest --cov-fail-under=85` in CI workflow |
| 15 | Hyperopt run cadence | **Manual trigger** via admin endpoint OR weekly (Sunday 06:00 UTC); writes results to MLflow registry |

---

## 3. Architecture

### 3.1 Module layout

```
backend/app/
├── ops/                                  NEW
│   ├── monitoring.py                     — Prometheus instrumentation hooks
│   ├── alerts.py                         — Email alert dispatcher
│   ├── champion_challenger.py            — Auto-promote ML checkpoints
│   └── verifier_scheduler.py             — Nightly audit verifier task
├── ml/
│   ├── champion_challenger.py            NEW — backtest-gated activation
│   └── ...
└── api/routes/
    ├── admin_monitoring.py               NEW — health summary + Grafana link
    └── admin_backtest.py                 NEW — trigger backtest / hyperopt

backend/tools/
├── backtest.py                           NEW — Freqtrade library wrapper
├── hyperopt.py                           NEW — Optuna search for layer weights
└── backup/                               NEW
    ├── snapshot.py                       — pg_basebackup wrapper
    ├── upload_b2.py                      — encrypt + upload to B2
    ├── rsync_laptop.py                   — sync to laptop SSD
    └── recovery_rehearsal.py             — quarterly restore-and-verify

infra/                                    NEW or extended
├── prometheus/
│   ├── prometheus.yml                    — scrape config (backend metrics)
│   └── alert_rules.yml                   — Sev-1/Sev-2 alert rules
├── grafana/
│   └── dashboards/
│       ├── trading_radar_overview.json
│       ├── adapters_health.json
│       ├── audit_chain_status.json
│       └── ml_checkpoint_history.json
├── loki/
│   └── loki.yml
└── promtail/
    └── promtail.yml

docker-compose.yml                        — extended to add prometheus, grafana, loki, promtail services
```

### 3.2 Backtest framework

`tools/backtest.py` — wraps Freqtrade-as-library:

```python
def run_backtest(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    start: datetime,
    end: datetime,
    layer_weights: dict[int, float] | None = None,  # None = equal weights
    enabled_layers: set[int] | None = None,         # None = all 10
    enabled_traps: set[str] | None = None,          # None = all 17
    initial_balance_usdt: float = 10000.0,
) -> BacktestResult:
    """Run a deterministic backtest over historical bars.

    Returns:
        BacktestResult(
            n_trades, win_rate, profit_factor, sharpe, max_drawdown,
            equity_curve, trade_log, params_hash,
        )
    """
```

Implementation:
- Pull OHLCV from Postgres (`ohlcv` table seeded by SP-3 adapters)
- For each closed bar, run `predict()` with the configured layer weights/enabled
- Simulate trades: open at predicted entry, close at SL/TP/timeout (mirror shadow trading logic from SP-0.5)
- Aggregate metrics
- Persist result row to new `backtests` table

### 3.3 Hyperopt for layer weights

`tools/hyperopt.py` — Optuna study:

```python
def hyperopt_layer_weights(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    train_window: tuple[datetime, datetime],
    val_window: tuple[datetime, datetime],
    n_trials: int = 100,
) -> HyperoptResult:
    """Search for layer weights maximizing val-set Sharpe."""
    def objective(trial):
        weights = {
            i: trial.suggest_float(f"w{i}", 0.0, 0.3) for i in range(1, 10)
        }
        # Normalize to sum=1
        total = sum(weights.values())
        weights = {k: v/total for k, v in weights.items()}
        # Run backtest on TRAIN window with these weights
        train_result = run_backtest(layer_weights=weights, start=train_window[0], end=train_window[1])
        # Validate on VAL window
        val_result = run_backtest(layer_weights=weights, start=val_window[0], end=val_window[1])
        return val_result.sharpe  # maximize

    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler())
    study.optimize(objective, n_trials=n_trials)
    return HyperoptResult(best_weights=study.best_params, best_sharpe=study.best_value, study=study)
```

Results land in MLflow (`mlflow.log_params({"w1": ..., "w2": ...})` per trial). Admin endpoint to view.

### 3.4 Audit chain verifier scheduler

`app/ops/verifier_scheduler.py` — nightly task in lifespan:

```python
async def run_audit_verifier_loop(session_factory):
    """Nightly at 03:00 UTC: verify hash chain integrity for each chained table.

    On any failure: log error + email admin + write to audit_violations table.
    """
    while True:
        await asyncio.sleep(_seconds_until_next_utc_hour(3))
        async with session_factory() as session:
            for table in ["predictions", "paper_trades", "shadow_trades"]:
                try:
                    result = await verify_chain(session, table)
                    if not result.ok:
                        log.error("audit chain BROKEN at %s row %s", table, result.first_broken_row_id)
                        await alert_admin(f"Audit chain broken: {table} row {result.first_broken_row_id}")
                        await session.execute(sa.text(
                            "INSERT INTO audit_violations (attempted_email, reason) VALUES (:e, :r)"
                        ), {"e": "system", "r": f"audit_chain_broken:{table}:{result.first_broken_row_id}"})
                        await session.commit()
                except Exception as e:
                    log.exception("audit verifier crashed for %s", table)
```

### 3.5 DR backup pipeline

`tools/backup/snapshot.py` — wrapper around `pg_basebackup`:

```python
def take_snapshot(out_dir: Path) -> SnapshotMetadata:
    """Run pg_basebackup against the running Postgres; tar.gz the output."""
    # subprocess.run(["pg_basebackup", "-D", str(out_dir), "-Ft", "-z", ...], check=True)
    return SnapshotMetadata(path=out_dir, size_bytes=..., taken_at=datetime.now(UTC))
```

`tools/backup/upload_b2.py` — encrypts + uploads:

```python
def upload_to_b2(snapshot_path: Path, *, encryption_key: bytes) -> None:
    """AES-256-GCM encrypt + upload to b2://trading-radar-backups/db-snapshots/<date>/"""
    # gpg --encrypt OR cryptography library
    # boto3.client("s3", endpoint_url=B2_ENDPOINT).upload_file(...)
```

`tools/backup/rsync_laptop.py` — rsyncs to laptop's external SSD via SSH:

```python
def rsync_to_laptop(snapshot_path: Path, *, target: str) -> None:
    """rsync -avz {snapshot_path} {target}"""
    # subprocess.run(["rsync", "-avz", str(snapshot_path), target], check=True)
```

Cron schedule (in `docker-compose.yml` or systemd):
- Hourly (xx:00): pg_dump of changed tables → Oracle local
- Nightly (00:30 UTC): pg_basebackup → B2 + laptop

### 3.6 Recovery rehearsal

`tools/backup/recovery_rehearsal.py` — quarterly automated test:

1. Pull latest B2 backup
2. Decrypt
3. Restore to a fresh Postgres container (different port to avoid conflict)
4. Run row-count assertion: `SELECT COUNT(*) FROM predictions` matches production within ±1 (allowing for 1 bar of drift)
5. Email admin success/failure

Triggered manually OR by quarterly cron (`0 12 1 */3 *` = noon on 1st of every 3rd month).

### 3.7 Monitoring stack

`docker-compose.yml` extended:

```yaml
  prometheus:
    image: prom/prometheus:v2.55.0
    volumes:
      - ./infra/prometheus:/etc/prometheus
      - prometheus_data:/prometheus
    ports: ["9090:9090"]
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.retention.time=30d'

  grafana:
    image: grafana/grafana:11.2.0
    volumes:
      - grafana_data:/var/lib/grafana
      - ./infra/grafana/dashboards:/etc/grafana/provisioning/dashboards
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_ADMIN_PASSWORD}
    ports: ["3000:3000"]

  loki:
    image: grafana/loki:3.2.0
    volumes:
      - ./infra/loki/loki.yml:/etc/loki/local-config.yaml
    ports: ["3100:3100"]

  promtail:
    image: grafana/promtail:3.2.0
    volumes:
      - /var/log:/var/log:ro
      - ./infra/promtail/promtail.yml:/etc/promtail/config.yml
```

`app/ops/monitoring.py`:

```python
from prometheus_fastapi_instrumentator import Instrumentator

def instrument_app(app: FastAPI) -> None:
    Instrumentator().instrument(app).expose(app, endpoint="/metrics")
```

Wire in `app/main.py:create_app()`.

### 3.8 Latency p99 < 500ms

Instrumentation already covered by `prometheus-fastapi-instrumentator`. SLO defined in `infra/prometheus/alert_rules.yml`:

```yaml
groups:
  - name: trading-radar-slo
    rules:
      - alert: HighLatency
        expr: histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[5m])) > 0.5
        for: 5m
        annotations:
          summary: "p99 latency > 500ms for 5min"
```

Alert routes to email via Alertmanager (or directly via Prometheus webhook).

### 3.9 Champion-challenger gate

`app/ml/champion_challenger.py`:

```python
async def evaluate_challenger(
    session: AsyncSession,
    challenger_checkpoint_id: int,
) -> ChampionChallengerResult:
    """Compare a candidate checkpoint against the current active champion.

    Runs `tools/backtest.py` with the challenger's predictions vs champion's,
    over a fixed held-out window (last 30 days NOT in either's training set).
    Returns: {champion_mae, challenger_mae, challenger_wins: bool}.

    challenger_wins iff challenger_mae < champion_mae * 0.95 (5% improvement bar).
    """
```

Wire into the existing admin checkpoint activation endpoint:
- When admin POSTs `/api/v1/admin/ml-checkpoints/{id}/activate`:
  - If `?force=true` query param: skip evaluation, activate immediately
  - Otherwise: run `evaluate_challenger`; activate only if `challenger_wins`

---

## 4. Data model

### 4.1 New tables

`backtests` (logs every backtest run):
```sql
CREATE TABLE backtests (
    id BIGSERIAL PRIMARY KEY,
    triggered_by BIGINT REFERENCES users(id),
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    start_ts TIMESTAMPTZ NOT NULL,
    end_ts TIMESTAMPTZ NOT NULL,
    layer_weights JSONB,
    enabled_layers JSONB,
    enabled_traps JSONB,
    initial_balance DOUBLE PRECISION NOT NULL,
    n_trades INTEGER NOT NULL,
    win_rate DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    sharpe DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    equity_curve_uri TEXT,                 -- B2 URI to JSON
    trade_log_uri TEXT,                    -- B2 URI to CSV
    params_hash TEXT NOT NULL              -- sha256 of all inputs for reproducibility
);
CREATE INDEX backtests_symbol_tf_idx ON backtests (symbol, timeframe, triggered_at DESC);
```

`hyperopt_studies`:
```sql
CREATE TABLE hyperopt_studies (
    id BIGSERIAL PRIMARY KEY,
    triggered_by BIGINT REFERENCES users(id),
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    n_trials INTEGER NOT NULL,
    train_window TSTZRANGE NOT NULL,
    val_window TSTZRANGE NOT NULL,
    best_weights JSONB,
    best_sharpe DOUBLE PRECISION,
    mlflow_run_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed'))
);
```

`backup_runs` (audit log of backup operations):
```sql
CREATE TABLE backup_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    backup_type TEXT NOT NULL CHECK (backup_type IN ('hourly_dump', 'nightly_basebackup', 'recovery_rehearsal')),
    target TEXT NOT NULL,                  -- 'oracle_local' | 'b2' | 'laptop' | 'rehearsal_restore'
    success BOOLEAN,
    size_bytes BIGINT,
    duration_seconds DOUBLE PRECISION,
    error_message TEXT
);
```

Migration 0012 creates these.

---

## 5. Validation procedure

1. **Backtest** — run `python tools/backtest.py --symbol BTC/USDT --tf 1h --start 2024-01-01 --end 2024-12-31 --weights equal` → returns sensible numbers (Sharpe between -2 and +2, max_drawdown 0-50%)
2. **Hyperopt** — run `python tools/hyperopt.py --n-trials 20 --train-start 2024-01-01 --train-end 2024-06-30 --val-start 2024-07-01 --val-end 2024-12-31` → completes + writes row to `hyperopt_studies`
3. **Audit verifier** — manually break a `predictions` row's `prev_hash`, run verifier, assert it detects the break + writes to `audit_violations`
4. **Backup** — run `python tools/backup/snapshot.py --out /tmp/test_backup` → creates valid pg_basebackup tarball; `python tools/backup/upload_b2.py --path /tmp/test_backup` → uploads to B2 (skip if B2 creds not configured locally)
5. **Recovery rehearsal** — run `python tools/backup/recovery_rehearsal.py --b2-key latest` → restores to throwaway container + asserts row counts
6. **Monitoring** — visit `http://localhost:3000` → Grafana login → see "Trading Radar Overview" dashboard with backend latency p50/p95/p99 panels
7. **Latency p99** — Grafana shows < 500ms; otherwise tune
8. **Champion-challenger** — register a stub checkpoint, attempt to activate without `?force=true`, expect rejection if MAE worse than current

---

## 6. Sub-project sequencing

SP-7 implementation order:

- **Phase A — Worktree + scaffolding + migration 0012 (backtests + hyperopt_studies + backup_runs)** (~5 tasks)
- **Phase B — Backtest framework (`tools/backtest.py` + tests)** (~5 tasks)
- **Phase C — Hyperopt (`tools/hyperopt.py` + Optuna integration + tests)** (~4 tasks)
- **Phase D — Audit verifier scheduler + alerts** (~4 tasks)
- **Phase E — Backup pipeline (snapshot + B2 upload + laptop rsync + recovery rehearsal)** (~6 tasks)
- **Phase F — Monitoring stack (docker compose + Prometheus + Grafana + Loki + dashboards)** (~5 tasks)
- **Phase G — Champion-challenger + admin endpoints + ship** (~5 tasks)

After SP-7 ships, the sub-projects roadmap is:
- **SP-1.1** — Train first Conv-LSTM checkpoint (user-paced)
- **SP-4** — RL Brain L10 (depends on SP-1.1)
- **SP-8** — Autonomous trading (depends on SP-4 + 30 days paper trading data)
- **SP-9** — News + sentiment (FinBERT for L9)
- **SP-3.5** — On-chain data adapters

---

## 7. Cross-cutting policy compliance

| Policy | How SP-7 satisfies it |
|---|---|
| §5.14 audit hash chain | Verifier scheduler runs nightly; alerts on any break |
| §5.13 backups | Hourly + nightly + B2 + laptop chain implemented |
| §2.6 Cloudflare Access | Admin monitoring + backtest endpoints inherit `Depends(require_admin)` |
| §2.7 Oracle host | Monitoring stack runs alongside backend in docker-compose; minimal resource footprint |
| Per-user (SP-0.7) | Backtests + hyperopts triggered by an admin; `triggered_by` user_id tracked |

---

## 8. Risk + fallback plan

| Failure mode | Detection | Fallback |
|---|---|---|
| Freqtrade-as-library API drift | Backtest tests fail on next pip update | Pin Freqtrade version; document |
| Optuna hyperopt finds degenerate weights (e.g., all weight on L1) | Sanity check on best_weights distribution | Add regularization term: penalize weights with stddev < 0.01 |
| pg_basebackup blocks on long-running queries | Backup runs > 60 min | Use `--no-pause` + accept slight inconsistency; or pause writes during backup window |
| B2 upload fails (rate limit / network) | backup_runs row with success=false | Retry with exponential backoff; alert admin if 3 consecutive failures |
| Grafana dashboard errors on missing metrics | Manual inspection | Provision dashboards via JSON; document required metric names |
| Latency p99 chronically > 500ms | Prometheus alert | Profile with `py-spy` + cprofile; tune slowest endpoint |
| Champion-challenger rejects all new models (too strict) | Manual review of comparison results | Loosen the 5% bar to 2% OR add `?force=true` admin override (already in design) |

**SP-7 failure does NOT brick the bot.** Existing prediction + shadow trading paths continue. Backups + monitoring are additive.

---

## 9. Acceptance criteria

- [ ] `tools/backtest.py` runs on a 6-month BTC/USDT 1h window, produces sensible Sharpe + max_drawdown
- [ ] `tools/hyperopt.py` completes 20-trial Optuna study, writes row to `hyperopt_studies`
- [ ] Nightly audit verifier runs at 03:00 UTC; manually-broken chain detected + alerted
- [ ] pg_basebackup → B2 upload + laptop rsync end-to-end works (test against real B2 if creds available)
- [ ] Recovery rehearsal script restores latest backup to throwaway container + asserts row counts match production within ±1
- [ ] Grafana dashboard "Trading Radar Overview" shows latency p50/p95/p99 panels with live data
- [ ] p99 latency < 500ms on /api/v1/predict and /api/v1/bot-status/* endpoints (measured over 5min window)
- [ ] Champion-challenger gate rejects a checkpoint with MAE worse than current; accepts one with MAE 5% better
- [ ] No regression in existing 1342+ backend tests
- [ ] At minimum 60+ new tests (backtest + hyperopt + verifier + backup + champion-challenger)

---

## 10. Implementation cost estimate

- Sub-project size: **~35 tasks across 7 phases**
- Wall-clock: **~4 weeks of subagent-driven work** (per meta-plan §3 §181)
- New backend modules: `app/ops/{monitoring, alerts, champion_challenger, verifier_scheduler}.py`, `app/ml/champion_challenger.py`, `app/api/routes/{admin_monitoring, admin_backtest}.py`
- New tools: `tools/{backtest, hyperopt}.py`, `tools/backup/{snapshot, upload_b2, rsync_laptop, recovery_rehearsal}.py`
- New infra: `infra/{prometheus, grafana, loki, promtail}/` + extended `docker-compose.yml`
- Database migrations: 1 (0012 — backtests + hyperopt_studies + backup_runs)
- New runtime deps: `optuna==4.0.0`, `freqtrade==2024.10` (as library), `prometheus-fastapi-instrumentator==7.0.0`
- New test count: ~60-80
- Frontend: minimal — only `Admin → Monitoring` sub-page link to Grafana (defer detailed dashboards to SP-7.5)

---

## 11. Open questions (resolved during implementation)

| # | Question | Resolved during |
|---|---|---|
| 1 | Should hyperopt run on full layer space or a subset (e.g., just L1+L3+L5 today, expand as L4/L6/L8 mature)? | Phase C — start with L1-L9 (excluding placeholders L7/L9/L10 which return None); document |
| 2 | Should backtest use the FULL universe (30 assets) or single symbol (BTC)? | Phase B — single symbol for v1; multi-asset backtest in SP-7.5 |
| 3 | Email alert SMTP server choice? | Phase D — operator configures via env vars (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`); recommend Postmark or SendGrid free tier |
| 4 | Should monitoring endpoints (`/metrics`) be auth-gated? | Phase F — yes, behind Cloudflare Access (production); no auth on local dev (LAN) |
| 5 | Champion-challenger held-out window — fixed dates or rolling? | Phase G — rolling: last 30d ALWAYS; assumes the candidate was trained on data ending ≥ 30d ago |

---

## 12. Reference

- Meta-plan: `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §2.7, §3 §181, §8 (monitoring stack)
- SP-0 backup baseline (pg_dump + B2 partial): `docs/superpowers/specs/2026-05-01-SP-0-tracer-bullet-design.md` (if exists)
- Freqtrade docs: https://www.freqtrade.io/en/stable/
- Optuna docs: https://optuna.org/
- prometheus-fastapi-instrumentator: https://github.com/trallnag/prometheus-fastapi-instrumentator

---

**END OF SP-7 OPS HARDENING DESIGN SPEC**
