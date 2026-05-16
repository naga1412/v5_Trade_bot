# PR1 — Record-only data attach + DB migration

**Status**: Design approved 2026-05-16. Implementation plan pending.
**Owner**: Backend (scoring + persistence + alembic).
**Parent**: 12-feature upgrade plan, 9-PR rollout.
**Predecessor**: ARCHITECTURE.md, PART H wiring audit, 6-item pre-plan investigation.

---

## 1. Goal

Land the foundation for the upgrade plan's analytics — **record-only** new metadata (MTF confluence, p_win, vol-normalized effective_score, funding-directional adjustment) plus the DB columns to store it. **Zero behavior change.** No gating, no new workers, no new feature enabled for live or telegram-approve modes.

PR1 also includes two structural cleanups on the critical path that PR1 itself depends on:
- `audit.py` whitelist refactor so adding columns is a no-op for the hash chain.
- `payload_builders.py` consolidation so the 4 production payload builders become 3 shared functions.

---

## 2. Scope (in PR1)

| ID | Feature | What lands |
|---|---|---|
| A1 + A2 | MTF confluence | `app/core/scoring/mtf_confluence.py` (compute) + aggregator attaches `mtf_agreement / mtf_dominant_tf / mtf_directions_json` to the result |
| C1 + C2 | p_win calibrator | `app/core/scoring/p_win_calibrator.py` (fit + predict, no worker yet) + aggregator attaches `p_win` (always None until PR5 worker fits) |
| D | Volatility normalization | aggregator computes `realized_vol_20d` + `effective_score`; recorded only, not used in gating |
| E | Funding directional adjustment | aggregator looks up most recent `intermarket_snapshots` row, computes signed `funding_directional_adj` |
| AUDIT-REFACTOR | `audit.py` whitelist | `HASH_PAYLOAD_COLUMNS` constant + verifier symmetry + consistency test |
| BUILDERS-REFACTOR | `payload_builders.py` | Extract 3 builders (predictions, shadow_trades, live_trades) before adding new keys |
| DB | Alembic migration | 7 new columns × 3 tables, plus `timeframe NOT NULL DEFAULT '1h'` on all 3 |
| TESTS | New + updated tests | 4 new test files for the scoring modules; 1 fixture-golden test per builder; 1 audit-whitelist consistency test; integration tests updated for null-passthrough |

## 3. Explicitly NOT in PR1

- A3 (dispatcher MTF gate) — PR2
- F (SHORT-specific safety flags + behaviour) — PR2
- B1-B4 (multi-resolution / 15m / pre-warm / narrow universe) — PR3
- G (IC auto-weighting, regime-conditional weights) — PR4
- H1-H4 (per-candle obs, schema v2, BC pretrain, reward shaping) — PR5
- I (RL ensemble + distillation) — PR7
- J (outcome-adaptive cooldown) — PR8
- K + L (dynamic sizing + true self-healing) — PR9
- The `p_win_recalibrate_task` worker — PR5
- Redis, distributed cache, cache stats, adaptive TTL, async-rewriting other aggregator parts

---

## 4. Components

### 4.1 `backend/app/db/audit.py` — whitelist refactor (lands FIRST)

```python
# audit.py — new module-level constant
HASH_PAYLOAD_COLUMNS: dict[str, frozenset[str]] = {
    "predictions":   frozenset({...}),  # EXACTLY current effectively-hashed keys
    "shadow_trades": frozenset({...}),
    "live_trades":   frozenset({...}),
    "paper_trades":  frozenset({...}),
}

async def insert_with_chain(session, table, payload):
    prev = await _last_row_hash(session, table)
    whitelist = HASH_PAYLOAD_COLUMNS.get(table)
    if whitelist is None:
        raise ValueError(
            f"Table {table!r} not in HASH_PAYLOAD_COLUMNS. "
            f"Hash-chained tables must be explicitly registered."
        )
    hashable = {k: v for k, v in payload.items() if k in whitelist}
    new_hash = compute_row_hash(prev, hashable)
    full = {**payload, "prev_hash": prev, "row_hash": new_hash}
    ...  # INSERT unchanged
```

**Bound from operator (Correction 1 — fail-secure on both branches):**
- Whitelist is **fail-secure** — forgetting a column means it isn't tamper-evident (visible in audit). The excluded-set alternative is fail-open — forgetting a column means it gets hashed and breaks the chain silently on next deploy. **Whitelist only.**
- Unknown table → **raises `ValueError`**, not silent full-payload hash. Per Correction 1: any caller of `insert_with_chain` for a non-whitelisted table is a bug we want surfaced. Architecture states exactly 4 hash-chained tables; the function only accepts those 4.
- `test_audit_whitelist_consistency.py` includes a case asserting the raise on unknown table name.
- The initial `HASH_PAYLOAD_COLUMNS` whitelist MUST equal exactly the set of keys that were effectively hashed BEFORE this PR. Verify by replaying `audit_verifier` on the last 100 rows of each chained table after the refactor — output `row_hash` must equal stored `row_hash` for every row. If even one diverges, the whitelist is wrong; STOP and report.
- All new PR1 columns (`mtf_*`, `p_win`, `effective_score`, `realized_vol_20d`, `funding_directional_adj`, `timeframe`) stay **OUT** of the whitelist. They will be added later only if a deliberate decision is made to make them tamper-evident, which would require a `hash_schema_version` bump.
- Verifier reads the same `HASH_PAYLOAD_COLUMNS` constant (single source of truth).
- **New consistency test**: walks each chained table's schema, fails if any column appears that isn't either in the whitelist or in an explicit `NON_HASHED_ALLOW_LIST: dict[str, frozenset[str]]` constant. Forces every future PR to consciously decide.

### 4.2 `backend/app/db/payload_builders.py` — new module (lands SECOND, before new columns)

```python
def build_predictions_payload(
    pred: LivePredictionOut, *, user_id: int,
    ghost_payload: dict[str, Any] | None = None,
) -> dict[str, Any]: ...

def build_shadow_trade_payload(
    pos: ShadowPosition, *, user_id: int,
    exit_price: float, exit_reason: ExitReason,
    closed_at: datetime, bars_held: int, inputs_hash: str,
) -> dict[str, Any]: ...

def build_live_trade_payload(
    proposal: SignalProposal, order: OrderResult, *,
    user_id: int, approved_via: Literal["auto", "telegram"],
    mode_at_open: str, extra_reasoning: dict[str, Any] | None = None,
    margin_usdt: float, leverage: int, opened_at: datetime,
) -> dict[str, Any]: ...
```

**Bound from operator:**
- Pure mechanical extraction. The 4 production call sites must produce **BIT-IDENTICAL** dicts after the refactor as before. **No "improvements" smuggled in.**
- If the 4 call sites currently DIVERGE in any column or value, do NOT silently align them. Report each divergence as: `(file:line, what differs, recommendation for canonical behavior)`. Wait for per-divergence decision before consolidating.
- **Single fixture-based test per builder**: construct a canonical input (frozen `Prediction` / `ShadowPosition` / `(SignalProposal, OrderResult)`), call builder, assert equality against a frozen-dict golden value. Future schema changes require updating the golden — forces visibility.
- Refactor order is mandatory: **(a) extract, (b) all existing tests pass against unchanged behavior, (c) THEN add new keys** in step 4.5.

**Call sites to migrate** (4 total):
1. [backend/app/ws/live_prediction.py:128-144](backend/app/ws/live_prediction.py#L128-L144) — predictions
2. [backend/app/shadow/persistence.py:124-148](backend/app/shadow/persistence.py#L124-L148) — shadow_trades
3. [backend/app/trading/execution/dispatcher.py:353-374](backend/app/trading/execution/dispatcher.py#L353-L374) — live_trades (auto path)
4. [backend/app/ops/telegram_polling.py:192-212](backend/app/ops/telegram_polling.py#L192-L212) — live_trades (telegram-approved path)

### 4.3 `backend/app/core/scoring/mtf_confluence.py` — new module

```python
@dataclass(frozen=True)
class MtfConfluence:
    agreement: int             # count of TFs agreeing with signal direction
    dominant_tf: str           # TF with strongest signal (highest ADX × |dir|)
    directions: dict[str, int] # {"5m": +1|-1|0, ...}

TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "1h", "4h", "1d", "1w")
CACHE_TTL_S: dict[str, int] = {"5m": 60, "15m": 60, "1h": 300, "4h": 3600, "1d": 3600, "1w": 3600}
TF_FETCH_TIMEOUT_S: float = 2.0

async def compute_mtf_confluence(
    symbol: str,                   # Binance native (BTCUSDT)
    signal_direction: Direction,   # LONG | SHORT | NEUTRAL
    http: httpx.AsyncClient,
    *,
    _now: Callable[[], float] = time.time,
    _cache: dict[tuple[str, str], _CacheEntry] | None = None,
) -> MtfConfluence | None: ...
```

**Bounds from operator:**
- **`asyncio.gather` with `return_exceptions=True`** for the 6 TF fetches. Any per-TF exception or timeout degrades to `None` for that TF only — never the whole result.
- Per-TF timeout: **2s default**.
- **For NEUTRAL signals (per Correction 4)**: `agreement = None`; `dominant_tf = TF with highest |signed_direction × ADX|` (or `None` if all 6 TFs voted 0). The `directions` dict is still recorded fully. This avoids the "agreement with what?" ambiguity and is forward-compatible with PR2's gating.
- **For LONG/SHORT signals**: `agreement = count of TFs whose vote matches `signal_direction`. `dominant_tf` = first TF in `TIMEFRAMES` order whose vote matches.
- **Source**: Binance SPOT REST `/api/v3/klines` (geoblock-safe from Hetzner Helsinki, matches shadow_worker convention).
- **Cache**: module-level dict `_KLINE_CACHE: dict[tuple[str, str], _CacheEntry]`. TTLs per the table above.
- Returns `None` only if ALL 6 TFs failed. Otherwise returns a partial result with the failed TFs noted as `0` (no vote).

### 4.4 `backend/app/core/scoring/p_win_calibrator.py` — new module

```python
P_WIN_MIN_TRADES_TO_FIT: int = 50
P_WIN_ROLLING_WINDOW: int = 500
P_WIN_MODEL_DIR: Path = Path("backend/app/data/p_win_models")

def fit_p_win_models(session: Session) -> dict[Direction, IsotonicRegression | None]: ...
async def predict_p_win(final_score: float, direction: Direction) -> float | None: ...
```

- Per-direction `sklearn.isotonic.IsotonicRegression`.
- Persisted to `{P_WIN_MODEL_DIR}/{long,short}.pkl` (gitignored — operational data, not source).
- `predict_p_win` lazy-loads model on first call; caches in-memory.
- In PR1: no worker yet → no model files → returns `None` always → recorded as NULL in predictions.

### 4.5 Aggregator hook — `backend/app/core/scoring/aggregator.py` + `backend/app/core/predictor.py`

The aggregator hook attaches **7 new top-level fields** to `LivePredictionOut` (MTF expanded to 3 separate columns to match the DB schema):

| Field | Type | Source |
|---|---|---|
| `mtf_agreement` | `int \| None` | `compute_mtf_confluence` — count matching direction for LONG/SHORT; **`None` for NEUTRAL signals (per Correction 4)** |
| `mtf_dominant_tf` | `str \| None` | TF with highest `\|signed_dir × ADX\|`; `None` if all 6 TFs voted 0 |
| `mtf_directions_json` | `dict[str, int] \| None` | `{"5m": +1, "15m": -1, "1h": 0, ...}` per-TF vote map |
| `p_win` | `float \| None` | `predict_p_win(final_score, direction)` — None until PR5 |
| `realized_vol_20d` | `float \| None` | 20-day stdev of daily log-returns; fall back to None if `<20d` history |
| `effective_score` | `float \| None` | `final_score × (0.02 / max(realized_vol_20d, 0.01))` — None if `realized_vol_20d` is None |
| `funding_directional_adj` | `float \| None` | Signed; +0.10 LONG-boosted, -0.10 SHORT-boosted, 0 in deadband, None on lookup failure |
| (existing `prediction_extras`) | unchanged | trap/calibration metadata stays in here |

**No change to `final_score` math** — these fields are recorded but do NOT feed back into the score (gating is PR2).

### 4.6 Cache pre-warm + TTL-refresh — registered workers (per Correction 2)

**Both pre-warm and TTL-refresh are registered in `app/ops/worker_registry.py`** so they have heartbeat-backed observability and supervisor coverage. No orphan tasks.

**`mtf_cache_prewarm_task`** (single-shot, non-stateful):
- `async def prewarm_cache(session_factory, *, deadline_seconds: float = 60.0) -> int`
- Lifespan-spawned at startup. Walks the current universe × 6 TFs, calls the same `compute_mtf_confluence` path (no shadow code).
- WorkerSpec: `liveness_query=None` (single-shot — watchdog skips staleness check by design, still listed in `/admin/workers`), `stateful=False`, `pending_heartbeat=True`.
- Logs `mtf_prewarm: start universe=N tfs=6` at start and `mtf_prewarm: done duration=Ms entries=X / cancelled at deadline=60s` at end.
- If incomplete at deadline → cancelled; cold-cache misses tolerated per fail-open.

**`mtf_cache_ttl_refresh_task`** (long-running, non-stateful, auto-restart safe):
- `async def run_mtf_cache_refresh_loop(session_factory, *, interval_s: int = 30) -> None`
- Wakes every 30s; scans cache for entries within 20% of TTL expiry; calls the fetch path to refresh.
- **Records heartbeat** to `worker_heartbeats` on each loop iteration (worker name `mtf_cache_ttl_refresh_task`).
- WorkerSpec: `liveness_query=HEARTBEAT`, `max_staleness_seconds=5 * 60` (30s cadence + 4.5min slack), `stateful=False`.
- **No** stampede protection, request coalescing, or distributed cache. Simple, transparent.
- Failure mode: refresh fetch fails → entry expires normally → next read pays cold-cache cost. No retry storms.

Both are wired into `app/main.py` lifespan via `start_mtf_cache_prewarm_task()` / `start_mtf_cache_ttl_refresh_task()`. `test_worker_registry_consistency.py` must still pass — both worker source modules contain `log.info(...)` (already verified in design).

### 4.7 Alembic migration

`backend/alembic/versions/2026_05_16_HHMM_pr1_record_only_columns.py`

**Per-table sequence** (3-step for `timeframe`, single-step for new nullable columns):

```python
def upgrade():
    # --- timeframe NOT NULL DEFAULT '1h' on predictions / shadow_trades / live_trades ---
    # predictions: column already exists, nullable. Backfill + flip.
    op.execute("UPDATE predictions SET timeframe='1h' WHERE timeframe IS NULL")
    op.alter_column("predictions", "timeframe",
                    existing_type=sa.String(8), nullable=False,
                    server_default="1h")
    # shadow_trades: same.
    op.execute("UPDATE shadow_trades SET timeframe='1h' WHERE timeframe IS NULL")
    op.alter_column("shadow_trades", "timeframe",
                    existing_type=sa.String(8), nullable=False,
                    server_default="1h")
    # live_trades: column does NOT exist yet. Add + backfill + flip.
    op.add_column("live_trades", sa.Column("timeframe", sa.String(8), nullable=True))
    op.execute("UPDATE live_trades SET timeframe='1h' WHERE timeframe IS NULL")
    op.alter_column("live_trades", "timeframe",
                    existing_type=sa.String(8), nullable=False,
                    server_default="1h")

    # --- 7 new nullable columns × 3 tables ---
    for tbl in ("predictions", "shadow_trades", "live_trades"):
        op.add_column(tbl, sa.Column("mtf_agreement", sa.SmallInteger(), nullable=True))
        op.add_column(tbl, sa.Column("mtf_dominant_tf", sa.String(8), nullable=True))
        op.add_column(tbl, sa.Column("mtf_directions_json", sa.JSON(), nullable=True))
        op.add_column(tbl, sa.Column("p_win", sa.Float(), nullable=True))
        op.add_column(tbl, sa.Column("effective_score", sa.Float(), nullable=True))
        op.add_column(tbl, sa.Column("realized_vol_20d", sa.Float(), nullable=True))
        op.add_column(tbl, sa.Column("funding_directional_adj", sa.Float(), nullable=True))

def downgrade():
    # Reverse order. Drop new columns first, then drop timeframe column from live_trades,
    # then restore nullability on predictions/shadow_trades.
    ...
```

**Row counts confirm no chunking needed:**
- `predictions`: 95
- `shadow_trades`: 20
- `live_trades`: 1

### 4.8 Tests

New files under `backend/tests/`:

| File | Coverage |
|---|---|
| `tests/core/scoring/test_mtf_confluence.py` | Mock httpx (`respx`). LONG/SHORT/NEUTRAL cases. REST timeout → None for that TF only (gather `return_exceptions=True`). Cache hit/miss/TTL behavior. `prewarm_cache` deadline cancellation. |
| `tests/core/scoring/test_p_win_calibrator.py` | `<50` closed trades of a direction → None. `≥50` → `IsotonicRegression`. `predict_p_win` returns None when no `.pkl`; returns ∈ `[0,1]` when loaded. Round-trip save/load. Both directions. |
| `tests/core/scoring/test_vol_normalization.py` | `effective_score = final × 0.02 / max(vol, 0.01)`. `vol > 0.02` → multiplier < 1. `vol < 0.01` → multiplier = 2 (capped by `MIN_VOL`). `vol = None` → `effective_score = None`. Both directions. |
| `tests/core/scoring/test_funding_directional.py` | `+0.10` when funding ≤ `-0.05%/8h`. `-0.10` when funding ≥ `+0.05%/8h`. `0` in deadband. `None` when no intermarket_snapshot row. Both directions. |
| `tests/db/test_payload_builders.py` | Golden-dict tests for all 3 builders. Tests against current behavior BEFORE new columns are added, then re-asserted AFTER (new keys appear as `None`/absent per builder contract). |
| `tests/db/test_audit_whitelist_consistency.py` | Walks each chained table's column schema. Fails if any column exists that isn't in `HASH_PAYLOAD_COLUMNS[table]` OR `NON_HASHED_ALLOW_LIST[table]`. |
| `tests/db/test_audit_replay_identity.py` | Replays `audit_verifier` on the last 100 rows of each table after the refactor. Asserts every `compute_row_hash` matches the stored `row_hash`. Catches whitelist drift. |
| `backend/scripts/bench_aggregator_latency.py` | (per Correction 3) Standalone benchmark script — not a pytest test. Runs N=500 score-computations on BTCUSDT with fixed bar fixtures. Two CLI modes (`--mtf-disabled` / `--mtf-recording`). Outputs `{p50_ms, p95_ms, p99_ms, n_samples, mode}` JSON to stdout. CI captures as artifact (60s timeout, non-gating). |

Each test file covers BOTH the active case AND the feature-disabled / no-data no-op path. LONG and SHORT both tested for the math features. NEUTRAL is tested explicitly for `mtf_confluence` (per Correction 4).

---

## 5. Decision points (from brainstorming, approved by operator)

| # | Question | Decision | Rationale |
|---|---|---|---|
| D1 | `realized_vol_20d` basis | 20-day stdev of DAILY log-returns (resample 1h bars to daily) | `VOL_NORM_TARGET=0.02` (2%) only makes sense at daily scale |
| D2 | `funding_directional_adj` sign | Signed REAL: +0.10 LONG-boost, -0.10 SHORT-boost, 0 in deadband | Single column, clear directional semantics |
| D3 | MTF kline source | Binance SPOT REST `/api/v3/klines` | Geoblock-safe; matches shadow_worker |
| D4 | MTF cache impl | In-memory module-level dict, TTL per TF tier | Single backend process; ~135 REST fetches/hr/symbol fits |
| D5 | MTF for NEUTRAL signals (per Correction 4) | LONG/SHORT: `agreement = count matching direction`. NEUTRAL: `agreement = None`, `dominant_tf = TF with highest \|signed_dir × ADX\|` or `None` if all 6 voted 0. `directions` dict always recorded fully. | Avoids "agreement with what?" ambiguity. Cleaner semantics for when PR2 starts gating on this. |
| D6 | New fields on `LivePredictionOut` | Top-level, NOT inside `prediction_extras` JSONB | Spec mandates new top-level columns |
| D7 | Audit chain handling | Whitelist refactor (fail-secure); new cols stay OUT of whitelist | Per operator bound 1 |

## 6. Bounds from operator (must be enforced exactly)

### 6.1 Audit.py whitelist (fail-secure only)
- Initial whitelist MUST equal current effectively-hashed columns.
- Verify by replaying `audit_verifier` on last 100 rows — any divergence → STOP + report.
- New PR1 columns stay OUT of whitelist.
- Consistency test fails on unfamiliar columns absent from both whitelist and `NON_HASHED_ALLOW_LIST`.

### 6.2 Payload builders (mechanical extraction only)
- BIT-IDENTICAL output before/after.
- DIVERGENCE handling: report each as `(file:line, what differs, recommendation)`, wait per-divergence decision.
- Golden-dict fixture test per builder.
- Order: extract → existing tests pass → THEN add new keys.

### 6.3 PR1 async + cache (3 sub-bounds)
- `asyncio.gather(return_exceptions=True)`, per-TF 2s timeout, per-TF None on failure.
- Pre-warm: background task, not blocking startup, hard 60s deadline, same `compute_mtf_confluence` path, fail-open if incomplete.
- TTL-refresh: every 30s, refresh entries within 20% of expiry, no stampede/coalescing/Redis, refresh failure = normal expiry.

### 6.4 Latency check gate (per Correction 3 — concrete, mechanically verifiable)

- New file: `backend/scripts/bench_aggregator_latency.py`
- Runs `N=500` score-computations on a fixed symbol (`BTCUSDT`) with fixed bar fixtures.
- Two modes via CLI flag: `--mtf-disabled` (baseline) and `--mtf-recording` (with MTF compute).
- Outputs JSON to stdout: `{"p50_ms": ..., "p95_ms": ..., "p99_ms": ..., "n_samples": 500, "mode": "<mode>"}`
- CI integration: runs as a smoke step in the backend CI job (timeout 60s); JSON captured as workflow artifact for visibility. **Not a CI gate** — captures numbers only.
- **PR1 merge gate** (operator review): `p50_recording - p50_baseline ≤ 50ms` AND `p99_recording - p99_baseline ≤ 200ms`. Numbers go in the PR description.
- If either gate fails → STOP, redesign before merge.

### 6.5 Hard out-of-scope (do NOT add in PR1)
- Redis or external cache.
- Cache warming for non-universe symbols.
- Adaptive TTL by volatility.
- Cache stats / Prometheus metrics (PR2).
- Async-rewriting of other aggregator parts.
- Behavioral gating (PR2).
- p_win_recalibrate_task worker (PR5).

---

## 7. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Whitelist drift (column added without consciously deciding hash status) | `test_audit_whitelist_consistency.py` fails CI |
| Payload builders silently change row content | `test_payload_builders.py` golden-dict + bit-identical replay before/after |
| MTF cold-cache p99 latency at HH:00:00 burst | `asyncio.gather` + pre-warm at startup + TTL-refresh background task |
| Migration on dirty `live_trades.timeframe` (currently nullable) | 3-step add → backfill → flip; only 1 row exists |
| `LivePredictionOut` breaks frontend | Pydantic default `extra='ignore'`; frontend grep confirms zero references to new field names; new fields are additive |
| L9 layer crashes during build_prediction | Existing `try/except + log.warning + return` path unchanged; PR1 doesn't touch L1-L10 internals |

## 8. Rollback

- `alembic downgrade -1` reverses all column additions + nullability changes in one step.
- Code rollback via standard PR revert. New modules (`mtf_confluence.py`, `p_win_calibrator.py`, `payload_builders.py`, `vol_normalization.py`, `funding_directional.py`, `bench_aggregator_latency.py`) are NEW files — revert deletes them cleanly.
- Audit whitelist refactor is NOT backwards-compatible in the partial-revert case (per Correction 1, fail-secure: unknown table raises `ValueError`). Full PR revert is required — don't partially revert just `audit.py` constants while keeping callers. Standard `git revert <merge-commit>` covers both.

## 9. Exit criteria (PR1 ships when)

1. ✅ All CI green (backend, frontend, docker-smoke).
2. ✅ mypy clean (398+ source files).
3. ✅ `test_audit_replay_identity.py` passes — last 100 rows of each chained table re-hash to their stored values.
4. ✅ Latency check gate passed — re-run benchmark shows p50 added ≤ 50ms, p99 added ≤ 200ms over baseline.
5. ✅ Migration applies + reverses cleanly on a copy of prod DB.
6. ✅ All 4 production call sites use the new `payload_builders.py` functions; grep confirms zero remaining inline payload dicts.
7. ✅ Spec-acceptance review against this doc by operator.

## 10. References

- Parent spec: 12-feature upgrade plan (in chat)
- `docs/ARCHITECTURE.md` — system map
- `MEMORY.md` entries: `dispatcher-outbound-telegram-was-unwired`, `dev-prod-branch-workflow`, `complete-modules-before-merge`
- PART H wiring audit findings (in chat, 2026-05-16)
- 6-item pre-plan investigation findings (in chat, 2026-05-16)
- Benchmark artifact: `tmp_bench/bench_pr1_latency.py` (delete after PR1 lands)
