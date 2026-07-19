# PR3 — Multi-resolution shadow (15m lane + prewarm + narrow universe)

**Status**: Design draft 2026-05-17. Awaiting operator review.
**Owner**: Backend (shadow worker + persistence + alembic + frontend tweak).
**Parent**: [Master rollout plan — Option D, 5 PRs](2026-05-17-master-rollout-plan-option-d.md).
**Predecessor**: PR2 MTF gate + SHORT safety (lands ~2026-05-27).
**Behavior change**: YES — adds a 15m lane to the shadow worker. Default config flip from `["1h"]` → `["1h", "15m"]` produces a ~4× shadow-signal rate that accelerates promotion-gate fills.

---

## 1. Goal

Add a 15m lane to the shadow worker alongside the existing 1h lane. Optionally narrow the symbol universe via `SHADOW_NARROW_UNIVERSE` for staging fast-iteration. Reuse PR1's MTF kline cache (`_KLINE_CACHE`) for bar warm-up to avoid duplicating bar buffers. Promotion-gate fills 4× faster — same 100-trade Sharpe / DD / win-rate / profit-factor criteria, just reached sooner.

PR3 also closes **the shadow_worker heartbeat gap (FU-1 partial)** and **tightens the watchdog window** to match 15m cadence. These are operationally mandatory for the 15m lane (a stalled 15m lane goes undetected for 2h under the current 2h staleness budget) and align with the `complete-modules-before-merge` memory.

PR3 does **NOT** change:
- Entry thresholds (±0.30 symmetric LONG/SHORT — PR #121).
- `final_score` math, scoring layer internals, MTF compute, p_win, vol-norm, funding-adj helpers.
- Live trading paths (PR2's gate + SHORT safety unchanged; PR3 is shadow-only).
- Frontend layout — only a tiny TF-aware hyperlink fix at `OpenPositions.tsx:36-38`.

---

## 2. Scope (in PR3)

| ID | Feature | What lands |
|---|---|---|
| B1 | 15m lane in shadow worker | `ShadowWorker` accepts a list of TFs; spawns one `MultiStreamReader` per TF; per-candle handler is TF-aware |
| B2 | Pre-warm reuses PR1 MTF cache | `setup()` reads bars from `mtf_confluence._KLINE_CACHE` if present, else REST-fetches and populates the cache. Single source of truth for 15m bars across MTF compute + shadow seed. |
| B3 | Narrow universe | `SHADOW_NARROW_UNIVERSE: list[str] = []` filters top-30 to subset if non-empty (e.g., staging uses `["BTCUSDT", "ETHUSDT"]` for fast iteration) |
| B4 | Per-TF cooldown | Schema migration: extend `shadow_cooldowns` PK to `(user_id, symbol, timeframe)`. Engine reads from `SHADOW_COOLDOWN_HOURS` dict |
| B4-OPEN | Per-TF open positions | Schema migration: replace `shadow_open_positions.symbol UNIQUE` with `(symbol, timeframe) UNIQUE`. Allows simultaneous 1h + 15m positions on the same symbol |
| B5 | Per-TF exit timeout | `exit_monitor.TIMEOUT_BARS` becomes a per-TF dict (`{"1h": 24, "15m": 96}`) — equal ~24h wall-clock |
| B6 | Shadow worker heartbeat | `record_heartbeat(name="shadow_worker")` inside `_handle_candle`. Removes `pending_heartbeat=True` from registry. Closes FU-1 partially (1 of 12) |
| B7 | Watchdog staleness budget tightening | `max_staleness_seconds` drops from `2*60*60` (2h, sized for 1h) to `30*60` (30 min, sized for 15m) |
| B8 | Persistence threads `timeframe` through | `ShadowPosition.timeframe`, `build_shadow_trade_payload(..., timeframe=...)`, `persist_closed_trade(..., timeframe=...)`. `shadow_trades.timeframe` (PR1 added the column, default `'1h'`) now reflects actual entry TF |
| B9 | Stats TF breakdown (read-only) | `/promotion-gate` JSON adds `per_timeframe: {"1h": {...}, "15m": {...}}` alongside the existing combined block. No frontend rendering change in PR3 — backend exposes the data |
| B10 | Frontend hyperlink fix | `OpenPositions.tsx:36-38` reads `pos.timeframe` instead of hardcoded `"1h"` in the chart deep-link |
| BENCH | Shadow latency bench | New `backend/scripts/bench_shadow_handle_candle.py`: N=200 candle-handling iterations per TF, measures p50/p99 of `_handle_candle`. Same V-7 budget shape (delta_p50 ≤ 50ms, delta_p99 ≤ 200ms) comparing 1h-only baseline vs 1h+15m enabled |
| TESTS | New + updated | 5 new unit tests, 2 new integration tests, 1 new migration test |

## 3. Explicitly NOT in PR3

- Live trading on 15m signals — `SHADOW_15M_ELIGIBLE_FOR_PROMOTION: bool = False` (default), gates whether 15m shadow_trades count toward promotion-gate. **Recording-mixed-TF, promoting-1h-only by default**; operator flips per-env after data validates 15m win-rate.
- Outcome-adaptive cooldown — PR8.
- Dynamic position sizing — PR9.
- New scoring or aggregator changes (PR3 reuses PR1+PR2's predictor unchanged).
- Frontend TF filters / TF breakdown UI in BotStatus tab — backend exposes data, frontend cosmetic ships in a follow-up.
- Closing remaining FU-1 worker heartbeats (PR3 only closes `shadow_worker`; the other 11 stay in FU-1).
- Closing FU-9 (httpx hygiene). Steady-state with PR1's cache reuse keeps PR3 well under V-7 budgets.
- Tightening `_KLINE_CACHE` cap from 200 → 300. PR3 elects to keep the MTF cache at 200 klines (its compute only needs the most recent ~80 for EMA/ADX); shadow seeds an additional 100-kline REST top-up at startup only. See §4.3 D4.

---

## 4. Components

### 4.1 `backend/app/config.py` — 5 new Settings fields

```python
class Settings(BaseSettings):
    ...
    # PR3: Multi-resolution shadow
    SHADOW_TIMEFRAMES: list[str] = ["1h", "15m"]
    SHADOW_PREWARM_BARS: int = 200
    SHADOW_COOLDOWN_HOURS: dict[str, float] = {"1h": 0.5, "15m": 0.5}
    SHADOW_NARROW_UNIVERSE: list[str] = []
    SHADOW_15M_ELIGIBLE_FOR_PROMOTION: bool = False
```

**Bounds from operator:**
- `SHADOW_TIMEFRAMES` defaults to `["1h", "15m"]` — the behavior flip from PR1/PR2's effective `["1h"]` (module constant).
- `SHADOW_COOLDOWN_HOURS` is a dict, not a single int. **This REPLACES the existing module-level `COOLDOWN_MINUTES=30` constant** in `worker.py:56`. **The 30-minute cooldown is preserved for both lanes** (`{"1h": 0.5, "15m": 0.5}` = 30 minutes each). The master rollout plan's "4h / 1h" dictation was reconciled to current 30-min default per operator decision 2026-05-17 (review of actual prod cooldown behavior preferred the current value over the conservative dictation). The dict shape future-proofs for asymmetric cooldowns later, even though both values are currently identical.
- `SHADOW_NARROW_UNIVERSE=[]` (empty list) = use full top-30 universe. Non-empty list = use intersection (any symbol in the list that's also in the top-30 universe). Filtering does NOT add symbols outside the top-30.
- `SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False` = 15m shadow trades are RECORDED but excluded from the promotion-gate's 100-trade Sharpe/DD/win-rate aggregates. Per spec §3 + master plan: "recording-mixed-TF, promoting-1h-only by default" pending data validation.
- Per-env override via env vars allowed for all 5 fields. Env vars use Pydantic v2 BaseSettings list/dict parsing (JSON-encoded for non-trivial types — document in field comments).

### 4.2 `backend/app/shadow/worker.py` — TF-aware refactor

The current `ShadowWorker` holds `bars: dict[str, pd.DataFrame]` keyed by symbol only and reads one `MultiStreamReader` for a single fixed TF. PR3 makes both dimensions TF-aware:

```python
class ShadowWorker:
    def __init__(self, symbols: list[str], session_factory, timeframes: list[str]):
        self.symbols = symbols
        self.timeframes = timeframes
        # bars now keyed by (symbol, tf)
        self.bars: dict[tuple[str, str], pd.DataFrame] = {}
        # one MultiStreamReader per TF (each opens its own combined WS stream)
        self.readers: dict[str, MultiStreamReader] = {
            tf: MultiStreamReader(symbols, timeframe=tf) for tf in timeframes
        }
        ...

    async def run(self) -> None:
        await self.setup()
        # merge candles from all TF streams via asyncio.gather of per-TF
        # loops, OR a single async-for over a merged stream. Implementation
        # detail covered in plan phase 2 — both are valid; choice is operator-approved.
        tasks = [
            asyncio.create_task(self._consume_one_tf(tf, reader))
            for tf, reader in self.readers.items()
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()

    async def _consume_one_tf(self, tf: str, reader: MultiStreamReader) -> None:
        async for candle in reader.stream():
            await self._handle_candle(candle, tf)
```

**Bounds from operator:**
- Single `ShadowWorker` instance with per-TF readers (NOT one worker per TF). Reason: shared state across TFs (open positions, cooldowns, MTF cache) stays consistent within one process. Per master plan + FU-1 rationale: avoid creating new `pending_heartbeat=True` workers.
- `_handle_candle` must be **TF-aware** — every internal call that depended on the module constant `SHADOW_TIMEFRAME` now takes the candle's TF explicitly. `worker.py:59, 96, 121, 273, 288, 444, 466` per the call-graph trace.
- Heartbeat fires once per `_handle_candle` invocation (B6) — exact line determined in plan phase.

### 4.3 `backend/app/shadow/worker.py` — bar buffer + pre-warm reuse PR1's `_KLINE_CACHE`

```python
async def setup(self) -> None:
    """Seed the per-(symbol, tf) bar buffer.

    Strategy: try to use mtf_confluence._KLINE_CACHE first; if present
    (TF=15m or 1h already prewarmed by PR1's mtf_cache_prewarm_task),
    reuse the kline list. Otherwise REST-fetch via BinanceClient and
    optionally populate the MTF cache so PR1's compute hits warm cache too.
    """
    from app.core.scoring.mtf_confluence import _KLINE_CACHE, _cache_get

    for tf in self.timeframes:
        for sym in self.symbols:
            cache_entry = _cache_get(sym, tf)
            if cache_entry is not None and len(cache_entry.klines) >= settings.SHADOW_PREWARM_BARS:
                klines = cache_entry.klines
            else:
                klines = await self.client.fetch_klines(
                    sym, tf, limit=settings.SHADOW_PREWARM_BARS
                )
                # Optionally populate MTF cache (best-effort; ignore failures)
                ...
            self.bars[(sym, tf)] = _klines_to_dataframe(klines)
```

**Decision points addressed:**
- **D1 — Cache reuse vs separate buffer**: REUSE. Both PR1 MTF compute and PR3 shadow seed need the same `(sym, tf)` klines; cache-of-cache is wasted memory.
- **D2 — KLINE_LIMIT vs SHADOW_PREWARM_BARS reconciliation**: MTF cache holds 200 klines (sufficient for EMA20/EMA50 + ADX). Shadow `build_prediction` historically wanted 300 (`HISTORY_BARS=300`). PR3 sets `SHADOW_PREWARM_BARS=200` (master plan default) and relies on `_handle_candle`'s rolling buffer to accumulate to ~300 as candles flow in. Trade-off: ~3 hours of cold-cache underweight for newly-listed symbols before the buffer fills. Acceptable per master plan.
- **D3 — When MTF cache misses, shadow REST-fetches AND populates MTF cache?**: Yes (best-effort, fail-open). One-shot benefit: subsequent MTF compute hits warm cache.

### 4.4 `backend/app/shadow/universe.py` — narrow-universe filtering

```python
async def load_shadow_universe(session: AsyncSession, narrow: list[str]) -> list[str]:
    """Load the active universe, intersect with SHADOW_NARROW_UNIVERSE if non-empty."""
    full = [e.symbol for e in await load_current_universe(session)]
    if not narrow:
        return full or ["BTCUSDT"]  # preserve existing empty-universe fallback
    intersect = [s for s in full if s in set(narrow)]
    if not intersect:
        log.warning(
            "SHADOW_NARROW_UNIVERSE=%r has no overlap with current universe %r; "
            "falling back to full universe to avoid empty worker",
            narrow, full[:5],
        )
        return full
    return intersect
```

**Bounds:**
- Empty `SHADOW_NARROW_UNIVERSE` → use full top-30 (PR1 behavior preserved).
- Non-empty with no overlap → log WARNING + fall back to full universe (fail-loud-then-fail-open). Reason: deploying to staging with a typo'd `SHADOW_NARROW_UNIVERSE=["BTCUSD"]` (wrong suffix) should not silently kill the worker.

### 4.5 `backend/app/shadow/persistence.py` — per-TF cooldowns + per-TF open positions

The schema currently has:
- `shadow_cooldowns PK (user_id, symbol)`
- `shadow_open_positions.symbol UNIQUE` (per-user not enforced by uniqueness — relies on app logic)

PR3 changes both to be TF-aware:

```python
async def set_cooldown(
    session, *, user_id: int, symbol: str, timeframe: str, until: datetime
) -> None:
    """UPSERT with (user_id, symbol, timeframe) PK."""
    ...

async def get_cooldowns(
    session, *, user_id: int
) -> dict[tuple[str, str], datetime]:
    """Returns dict keyed by (symbol, timeframe). Empty dict if user has none."""
    ...
```

`PositionGate.is_blocked(symbol, timeframe, now)` consults both the per-(symbol, tf) cooldown dict and the per-(symbol, tf) open positions. **A 1h position on BTC no longer blocks a 15m position on BTC** (the core motivation for PR3).

**Bounds:**
- Migration MUST backfill `timeframe='1h'` on all existing `shadow_cooldowns` rows before extending the PK (mirrors PR1's 3-step pattern for `live_trades.timeframe`).
- Migration MUST backfill `timeframe='1h'` on `shadow_open_positions` before changing the uniqueness constraint.
- Cross-TF duplicate-direction signal handling: §6.2 covers the R2 mitigation.

### 4.6 `backend/app/shadow/exit_monitor.py` — per-TF timeout

```python
# was: TIMEOUT_BARS = 24  # 24h on 1h
# becomes:
TIMEOUT_BARS_PER_TF: dict[str, int] = {"1h": 24, "15m": 96}  # ~24h wall-clock per TF
```

**Bounds:**
- Equal ~24h wall-clock holdtime across TFs is the policy choice. Alternatives (different wall-clock per TF) are out-of-scope; if PR2's MTF gate + this 24h ceiling produce stale 15m positions, that's a tuning question for a follow-up PR.
- The `exit_monitor` call site reads from the dict via `TIMEOUT_BARS_PER_TF[position.timeframe]`; KeyError on unknown TF is a programming error (fail-loud).
<<<<<<< HEAD
=======
- **PR3 §4.6b layers Hold/TP scaling on top of the per-TF base**: when `HOLD_TP_SCALING_ENABLED=True` AND `position.timeout_bars` is set on the position at open-time, `exit_monitor` reads `position.timeout_bars` instead of the per-TF default. The per-TF dict is the baseline; the per-position field is the multiplier-applied override.

### 4.6b `backend/app/shadow/{worker,exit_monitor}.py` — Hold/TP scaling by `mtf_agreement` (G1)

Originally part of PR4's "smart-position v1" trio (G1/G2/G3). G2 (IC auto-weighting) and G3 (regime-conditional weights) stay deferred — both require 30+ days of shadow data. G1 has no such dependency: it uses `mtf_agreement` already populated on `predictions` from PR1, so it lands in PR3 alongside the 15m lane.

**Behavior**: at trade-open, the worker looks up `mtf_agreement` for the entering signal and scales BOTH the `timeout_bars` and the `take_profit_price` per a fixed table. Stop-loss is **unchanged** (per-trade risk stays constant; only reward + hold-time scale up with multi-TF conviction).

```python
HOLD_TP_SCALING_ENABLED: bool = False  # default OFF; per-env enable after staging
HOLD_TP_SCALING_TABLE: dict[int, tuple[int, float]] = {
    # mtf_agreement: (timeout_bars, tp_multiplier)
    3: (24,  1.0),    # baseline (same as un-scaled 1h)
    4: (48,  1.25),
    5: (96,  1.5),
    6: (168, 2.0),
}
# None or < 3 → not reached (PR2 MTF gate blocks before this scaling lookup)
```

**Bounds:**
- Default OFF — `HOLD_TP_SCALING_ENABLED=False` reproduces PR2 behavior bit-identically. Operator flips per-env after staging validates.
- Per-trade risk (stop-loss distance) is INVARIANT under scaling. Only TP distance widens and timeout extends.
- Table values are scaling factors for the **1h-baseline** `timeout_bars=24`. For 15m positions, the spec applies the same multipliers against the per-TF baseline `timeout_bars=96`: e.g. `mtf_agreement=4` on 15m → `96 × (48/24) = 192` bars. Plan phase formalizes this via `effective_timeout_bars(tf, mtf_agreement)`.
- `mtf_agreement is None` (PR1 fail-open path) → no scaling applied; baseline timeout + 1.0× TP. The PR2 gate has already passed in this case, so the position opens as it would without scaling.
- `take_profit_price` is computed as `entry_price ± (tp_multiplier × baseline_tp_distance)` where the sign matches direction. Baseline TP comes from the existing signal generation (`engine.py`'s `_compute_targets`).

**ShadowPosition fields added**:
```python
@dataclass(frozen=True)
class ShadowPosition:
    ...
    # G1: when scaling is ON, these record the actual (scaled) values used
    # for this position. NULL when scaling is OFF (fall back to per-TF default).
    # Recording-only — out of HASH_PAYLOAD_COLUMNS per policy.
    hold_scaling_factor: float | None = None      # the tp_multiplier looked up
    hold_timeout_bars: int | None = None           # the timeout_bars actually used
```

**`shadow_trades` + `live_trades` columns added** (PR3 alembic migration):
```python
op.execute("ALTER TABLE shadow_trades ADD COLUMN hold_scaling_factor REAL NULL;")
op.execute("ALTER TABLE shadow_trades ADD COLUMN hold_timeout_bars   SMALLINT NULL;")
op.execute("ALTER TABLE live_trades   ADD COLUMN hold_scaling_factor REAL NULL;")
op.execute("ALTER TABLE live_trades   ADD COLUMN hold_timeout_bars   SMALLINT NULL;")
```
- Both NULL by default. Worker writes them on close-trade persistence; only non-NULL when scaling was active for that trade.
- **NOT** added to `HASH_PAYLOAD_COLUMNS` — recording-only per policy (matches `mtf_*` from PR1, `p_win` etc.).
- **YES** added to `NON_HASHED_ALLOW_LIST` on both tables, so the audit verifier doesn't mark them as missing hash inputs.
- Forward-compat: `live_trades` gets the columns now (PR3), so when the operator later flips `users.trading_mode` to `fully-auto`, PR2's `_place_live_order` + telegram-approve path can be wired to populate them in a future PR without another schema migration. PR3 itself only POPULATES them on the shadow path.

**Worker hook**:
```python
# In ShadowWorker._handle_candle, at the trade-open path:
from app.shadow.scaling import effective_hold_tp

if settings.HOLD_TP_SCALING_ENABLED:
    timeout_bars, tp_mult = effective_hold_tp(
        timeframe=tf, mtf_agreement=signal.mtf_agreement,
        table=settings.HOLD_TP_SCALING_TABLE,
    )
else:
    timeout_bars, tp_mult = (TIMEOUT_BARS_PER_TF[tf], 1.0)

new_tp = entry_price + (tp_mult * baseline_tp_distance) * sign
pos = ShadowPosition(
    ...,
    take_profit=new_tp,
    hold_scaling_factor=tp_mult if settings.HOLD_TP_SCALING_ENABLED else None,
    hold_timeout_bars=timeout_bars if settings.HOLD_TP_SCALING_ENABLED else None,
)
```

**Tests** (4-5 new):
- `test_hold_tp_scaling_lookup_per_agreement` — table lookup returns expected tuples for agreement 3/4/5/6; ValueError or fail-open None for values outside the table.
- `test_hold_tp_scaling_applies_to_position_open` — with flag ON + `mtf_agreement=5`, opened position has `hold_timeout_bars=96` (for 1h) AND `take_profit_price` = entry + 1.5× baseline TP distance.
- `test_hold_tp_scaling_disabled_default_24bar_1x` — flag OFF reproduces pre-PR3 1h behavior (24 bars, 1.0× TP); `hold_scaling_factor` and `hold_timeout_bars` columns stay NULL on `shadow_trades`.
- `test_hold_tp_scaling_neutral_signal_no_scaling` — NEUTRAL direction never opens a position so scaling never fires (assertion-only test, locks contract).
- `test_hold_tp_scaling_15m_applies_multiplier_against_per_tf_baseline` — flag ON + `mtf_agreement=4` + `tf=15m` → `hold_timeout_bars = 96 × 2 = 192` (the multiplier is relative to the TF baseline, not absolute).

**Bounds (G1-specific)**:
- G2 (IC auto-weighting) and G3 (regime-conditional weights) stay deferred to v2 evaluation queue — they need 30+ days of MTF shadow data which only starts accruing post-PR3 deploy. G1 has no such dependency and ships here.
- The scaling table is a `dict[int, tuple[int, float]]` — JSON-encoded via Pydantic v2 BaseSettings for env overrides. Document this in the field comment.
- Future tuning (different multipliers per TF, smoother curves, mtf_agreement=6 weight changes) is operator-deferred. PR3 ships the fixed table from the spec.
>>>>>>> origin/dev

### 4.7 Heartbeat + watchdog wiring (B6 + B7)

```python
# In _handle_candle:
async def _handle_candle(self, candle: Candle, tf: str) -> None:
    await self._append_bar(candle, tf)
    ...
    await record_heartbeat(self.session_factory, "shadow_worker")  # B6
```

```python
# In backend/app/ops/worker_registry.py — shadow_worker entry:
WorkerSpec(
    name="shadow_worker",
    ...,
    max_staleness_seconds=30 * 60,  # B7: was 2*60*60; sized for 15m cadence
    stateful=True,
    pending_heartbeat=False,  # B6: was True; heartbeat now wired
)
```

**Bounds:**
- B6 + B7 are non-negotiable for PR3 — running a 15m lane with a 2h watchdog window is operationally unacceptable.
- Heartbeat fires per `_handle_candle` invocation (not per TF-stream-iteration). Reason: a single 15m candle arrival is enough to demonstrate liveness across both TF streams (the worker is a single-process consumer of merged streams).

### 4.8 `backend/app/api/routes/bot_status.py` — TF breakdown on `/promotion-gate`

```python
@router.get("/promotion-gate")
async def promotion_gate(...) -> dict:
    """Existing 100-trade Sharpe/DD/win-rate gate.

    PR3: adds `per_timeframe` field showing the same metrics computed
    separately for each TF. Combined block continues to exclude 15m
    when SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False.
    """
    return {
        # existing combined fields (filter applies SHADOW_15M_ELIGIBLE_FOR_PROMOTION):
        "trades_total": ...,
        "sharpe": ...,
        "max_drawdown": ...,
        "win_rate": ...,
        "profit_factor": ...,
        "gate_status": ...,  # one of: pending|passed|failed
        # PR3 additions:
        "per_timeframe": {
            "1h":  {"trades_total": ..., "sharpe": ..., ...},
            "15m": {"trades_total": ..., "sharpe": ..., ...},
        },
    }
```

**Bounds:**
- Promotion-gate's `gate_status` continues to compute on the **combined-filtered** dataset (i.e., 15m excluded when `SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False`). The `per_timeframe` block is **informational only** — surfaces the 15m data to the operator without changing the promotion criterion.
- Frontend rendering of `per_timeframe` ships in a follow-up PR. PR3 only exposes the data.

### 4.9 `frontend/src/tabs/BotStatus/OpenPositions.tsx` — TF-aware deep link

Single 3-line change at lines 36-38:

```typescript
// was:
const chartHref = buildLivePredictionHash(pos.symbol, "1h");
// becomes:
const chartHref = buildLivePredictionHash(pos.symbol, pos.timeframe ?? "1h");
```

**Bounds:**
- `pos.timeframe` should already be on the OpenPosition record (it's in the DB row from PR1's column). If the API serializer doesn't surface it, PR3 adds it to the Pydantic response model.
- `?? "1h"` fallback preserves behavior for any pre-PR3 position rows lacking a timeframe.
- No other frontend changes in scope.

### 4.10 Alembic migration

`backend/alembic/versions/2026_06_XX_XXXX_pr3_shadow_per_tf_cooldown.py`

```python
def upgrade():
    # Step 1: shadow_cooldowns — extend PK to include timeframe
    # 1a. add column nullable
    op.execute("ALTER TABLE shadow_cooldowns ADD COLUMN timeframe VARCHAR(8) NULL;")
    # 1b. backfill existing rows with '1h'
    op.execute("UPDATE shadow_cooldowns SET timeframe = '1h' WHERE timeframe IS NULL;")
    # 1c. NOT NULL + DEFAULT '1h'
    op.execute("ALTER TABLE shadow_cooldowns ALTER COLUMN timeframe SET NOT NULL;")
    op.execute("ALTER TABLE shadow_cooldowns ALTER COLUMN timeframe SET DEFAULT '1h';")
    # 1d. drop old PK, create new PK
    op.execute("ALTER TABLE shadow_cooldowns DROP CONSTRAINT shadow_cooldowns_pkey;")
    op.execute(
        "ALTER TABLE shadow_cooldowns "
        "ADD CONSTRAINT shadow_cooldowns_pkey PRIMARY KEY (user_id, symbol, timeframe);"
    )

    # Step 2: shadow_open_positions — replace symbol UNIQUE with (symbol, timeframe) UNIQUE
    # 2a. add column nullable
    op.execute("ALTER TABLE shadow_open_positions ADD COLUMN timeframe VARCHAR(8) NULL;")
    # 2b. backfill
    op.execute("UPDATE shadow_open_positions SET timeframe = '1h' WHERE timeframe IS NULL;")
    # 2c. NOT NULL + DEFAULT '1h'
    op.execute("ALTER TABLE shadow_open_positions ALTER COLUMN timeframe SET NOT NULL;")
    op.execute("ALTER TABLE shadow_open_positions ALTER COLUMN timeframe SET DEFAULT '1h';")
    # 2d. drop old UNIQUE constraint
    op.execute("ALTER TABLE shadow_open_positions DROP CONSTRAINT shadow_open_positions_symbol_key;")
    # 2e. add new composite UNIQUE
    op.execute(
        "ALTER TABLE shadow_open_positions "
        "ADD CONSTRAINT shadow_open_positions_symbol_tf_key UNIQUE (symbol, timeframe);"
    )

def downgrade():
    # Reverse order. The downgrade is tested per FU-10 follow-up.
    ...
```

**Bounds:**
- Migration test in `backend/tests/db/test_pr3_migration.py` mirrors PR1's `test_pr1_migration.py` pattern (Postgres-only, schema introspection).
- Row counts (operator confirms before merge): `shadow_cooldowns` ≈ low-tens, `shadow_open_positions` ≤ 30 (one per symbol). Backfill is trivial.
- Constraint names follow Postgres defaults (`<table>_pkey`, `<table>_<col>_key`); the migration uses raw SQL because alembic's autogenerate doesn't handle PK changes cleanly.

### 4.11 Tests

| File | Coverage |
|---|---|
| `tests/shadow/test_worker_multi_tf.py` (NEW) | Worker accepts 2 TFs; spawns 2 readers; per-(sym, tf) bars; per-(sym, tf) cooldowns; `_handle_candle` routes correctly. |
| `tests/shadow/test_universe_narrow.py` (NEW) | Empty list → full universe; non-empty intersects; no overlap → WARN + fallback to full. |
| `tests/shadow/test_cooldown_per_tf.py` (NEW) | 1h cooldown does not block 15m; 15m cooldown does not block 1h; same-TF cooldown blocks same-TF. |
| `tests/shadow/test_open_positions_per_tf.py` (NEW) | 1h open does not block 15m open on same symbol; same-TF blocks same-TF. |
| `tests/shadow/test_exit_monitor_per_tf.py` (NEW) | 15m position expires at 96 bars; 1h position expires at 24 bars. |
| `tests/db/test_pr3_migration.py` (NEW) | Postgres schema introspection: `shadow_cooldowns` PK = `(user_id, symbol, timeframe)`; `shadow_open_positions` UNIQUE = `(symbol, timeframe)`; backfill correct. |
| `tests/integration/test_pr3_e2e_dual_lane.py` (NEW) | End-to-end: WS frames arrive on both TF streams; worker writes one `shadow_trades` row per TF on entry signal; same-symbol-cross-TF works. |
| `tests/integration/test_pr3_promotion_gate_breakdown.py` (NEW) | `/promotion-gate` JSON includes `per_timeframe`; combined block excludes 15m when `SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False`. |
| `tests/ops/test_shadow_worker_heartbeat.py` (NEW) | `record_heartbeat("shadow_worker")` fires inside `_handle_candle`; watchdog `max_staleness_seconds=30*60` enforced. |
| `backend/scripts/bench_shadow_handle_candle.py` (NEW) | V-7 bench for `_handle_candle` latency — 1h-only baseline vs 1h+15m. Budget: delta_p50 ≤ 50ms, delta_p99 ≤ 200ms. |
| `tests/db/test_payload_builders.py` (UPDATE) | Existing golden-dict tests grow `timeframe` parameter; default `'1h'` (PR1 compat). New cases: `timeframe='15m'` round-trips correctly. |

---

## 5. Decision points (carried forward from master plan + research)

| # | Question | Decision | Rationale |
|---|---|---|---|
| D1 | One worker for both TFs, or two separate workers? | One worker, per-TF readers | Single source of state (open positions, cooldowns, cache); avoids new `pending_heartbeat=True` workers per FU-1. |
| D2 | Reuse PR1's `_KLINE_CACHE` or duplicate? | REUSE | Same Binance SPOT REST endpoint, same `(sym, tf)` keys, 15m TTL=60s aligns naturally with bar period. |
| D3 | KLINE_LIMIT bump (200 → 300)? | NO — shadow seeds 200, lets buffer accumulate to ~300 over candle flow | Bumping cap is out-of-scope churn; 200 is sufficient for EMA20/50 + ADX (MTF compute's own need). |
| D4 | Per-TF cooldown vs shared cooldown? | Per-TF (dict shape preserved; both values 30 min) | The dict-shape future-proofs for asymmetric cooldowns. Operator reconciled values to `{"1h": 0.5, "15m": 0.5}` (= 30 min each) on 2026-05-17 — preferred current prod cooldown over master plan's "4h / 1h" dictation. |
| D5 | Per-TF open positions vs shared? | Per-TF | The motivation of PR3 is "4× signal rate"; shared open-positions defeats this. |
| D6 | 15m exit timeout | 96 bars (~24h wall-clock match with 1h's 24-bar window) | Symmetric wall-clock policy; tunable post-launch. |
| D7 | 15m signals eligible for live promotion | NO by default (`SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False`) | Per master plan: record 15m to gather data, but don't gamble promotion criterion on noisier TF until win-rate proven. |
| D8 | Frontend TF rendering scope | Minimal — only the deep-link fix at `OpenPositions.tsx:36-38` | Bigger TF-aware UI (filters, breakdown rendering) is operator-deferred to a follow-up; backend exposes the data. |
| D9 | Watchdog staleness budget | 30 min (`30*60` seconds) | 2× the 15m bar cadence — same factor as the old 2h budget against the 1h cadence. |
| D10 | Heartbeat cadence | Per `_handle_candle` invocation | Single-process worker; one candle from any TF demonstrates liveness across all TFs. |
| D11 | Cross-TF duplicate signal handling | Allow both; the trade record carries the TF; promotion-gate filter applies per-TF | Mitigation for R2; tested in `test_pr3_e2e_dual_lane.py`. |
| D12 | Migration row counts | Backfill in-place; no chunking | `shadow_cooldowns` low-tens, `shadow_open_positions` ≤ 30; same shape as PR1's live_trades.timeframe migration. |

## 6. Bounds from operator (must be enforced exactly)

### 6.1 Default-OFF discipline (recording-only ramp)
- `SHADOW_TIMEFRAMES=["1h", "15m"]` defaults to 4× signal rate. **Recording behavior change ON by default**; live-trading impact OFF by default (`SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False` and PR2's MTF gate operates on per-prediction MTF, not per-shadow-TF).
- All other PR3 flags have neutral defaults: `SHADOW_NARROW_UNIVERSE=[]` (full universe), `SHADOW_PREWARM_BARS=200` (same as MTF cache cap).

### 6.2 Cross-TF duplicate signal mitigation (R2)
- A LONG signal firing on 1h AND 15m for BTC near-simultaneously must produce **two distinct `shadow_trades` rows** with different `timeframe` values — not a deduplicated single row.
- The promotion-gate filter must NOT silently de-dup cross-TF trades.
- Documentation: this is intentional — both TFs are independent signal generators.

### 6.3 Heartbeat + watchdog hygiene (FU-1 partial)
- `shadow_worker.pending_heartbeat=True` flag MUST be removed when heartbeat is wired (B6).
- `max_staleness_seconds=2*60*60` MUST be tightened to `30*60` (B7).
- The two changes ship together in one commit — never wire one without the other.

### 6.4 Schema migration discipline (mirrors PR1)
- 4-step pattern for column flip: add nullable → backfill → SET NOT NULL → SET DEFAULT.
- Constraint changes use raw SQL (alembic autogenerate is unreliable for PK/UNIQUE changes).
- Migration test in CI introspects post-state.
- **FU-10 (downgrade-untested) reminder**: PR3's downgrade MUST be tested explicitly per the new pattern that FU-10 prescribes. Add `test_pr3_migration_downgrade_round_trip` even if it duplicates FU-10's queued work — PR3 is a non-trivial migration and the rollback path matters.

### 6.5 Bench gate (V-7 same budgets as PR1)
- Single-candle `_handle_candle` latency:
  - `delta_p50 = p50(1h+15m enabled) - p50(1h-only baseline) ≤ 50ms`
  - `delta_p99 ≤ 200ms`
- Run via `backend/scripts/bench_shadow_handle_candle.py --mode=baseline` and `--mode=multi-tf`.
- The 4× signal rate is **expected to nearly double DB write traffic + nearly double MTF cache lookups** — but cache lookups are O(1) µs and DB writes are async. p50 + p99 should both be well under budget.

### 6.6 No score / threshold changes
- Symmetric LONG/SHORT thresholds in `shadow/engine.py:18-19` (`LONG_THRESHOLD=0.30`, `SHORT_THRESHOLD=-0.30`) are NOT modified.
- `MIN_CONFIDENCE=0.50` is NOT modified.
- `_SHORT_DIRECTION_PENALTY` / `SHORT_BIAS_PP` (already protected by PR2 §6.2) remain untouched.

### 6.7 Frontend scope discipline
- The ONLY frontend change is the 3-line hyperlink fix at `OpenPositions.tsx:36-38`.
- No new pages, no TF-filter dropdowns, no breakdown rendering of `per_timeframe`. Defer.

### 6.8 Hard out-of-scope
- Outcome-adaptive cooldown (PR8).
- Dynamic position sizing (PR9).
- Self-healing supervisor / remaining FU-1 worker heartbeats (PR9).
- Cache warming for non-universe symbols.
- 5m or 4h lanes — PR3 ships only 15m alongside 1h.
- p_win retraining on 15m data (PR5 deferred).

---

## 7. Risks + mitigations

| Risk | Mitigation |
|---|---|
| **R1** 15m TF noisier than 1h → win-rate drops despite 4× signals | `SHADOW_15M_ELIGIBLE_FOR_PROMOTION=False` default — promotion-gate excludes 15m until staging shows win-rate ≥ 1h's |
| **R2** Same direction fires near-simultaneously on 1h + 15m → spurious duplicate trades | Per-TF rows by design (§6.2); `per_timeframe` breakdown in `/promotion-gate` lets operator spot cross-TF correlation |
| **R3** DB write load 4× higher | Composite index `(symbol, timeframe, opened_at DESC)` exists from PR1; async writes; `shadow_trades` row size is small. No mitigation needed beyond monitoring. |
| **R4** Bar-fetch rate 4× higher (15m bars refresh every 60s) → httpx pressure | RateLimitedClient has 1200/min capacity (Binance docs floor); 4× refresh × 30 symbols ≈ ~120 fetches/min — well under cap. |
| **R5** Watchdog drift if heartbeat not wired before deploying 15m | §6.3 hard bound — B6 + B7 ship together; CI test asserts the registry entry has `pending_heartbeat=False`. |
| **R6** Migration backfill leaves rows with wrong `timeframe='1h'` for what should be 15m | Pre-deploy state has zero 15m positions (the feature doesn't exist yet) — backfilling all to '1h' is correct by construction. |
| **R7** `SHADOW_NARROW_UNIVERSE` typo'd in env (e.g., `["BTCUSD"]` missing T) silently kills worker | §4.4 — empty intersection logs WARN and falls back to full universe. Tested in `test_universe_narrow.py`. |
| **R8** PR1 MTF cache miss for narrow-universe-only symbols | If `SHADOW_NARROW_UNIVERSE` adds a symbol not in PR1 prewarm's universe (impossible by spec — narrow is subset of universe), MTF cache miss → REST fetch. Worst case: extra ~200ms at startup for one symbol. Not a budget concern. |
| **R9** `per_timeframe` block on `/promotion-gate` breaks the existing frontend that doesn't expect it | Frontend ignores unknown JSON keys (current TanStack Query setup tolerates additive payloads); new keys are additive. Confirmed by reading the existing `BotStatus` queries. |
| **R10** `exit_monitor` per-TF dict KeyError if a new TF appears in `shadow_open_positions` without being in `TIMEOUT_BARS_PER_TF` | Programming-error fail-loud; new TFs require an explicit `TIMEOUT_BARS_PER_TF` entry. Acceptable: out-of-scope TFs (5m, 4h) don't ship in PR3. |

## 8. Rollback

**Two-stage rollback** (graceful → forceful):

**Stage 1 — disable 15m lane (no schema change)**:
- Set `SHADOW_TIMEFRAMES=["1h"]` in env. Worker spawns only the 1h reader.
- The 15m-specific columns in `shadow_cooldowns` and `shadow_open_positions` remain (now NULL for any future 1h-only rows, but the migration's NOT NULL constraint stays — new 1h-only rows fill `timeframe='1h'` via the DEFAULT).
- No DB downgrade needed.

**Stage 2 — full PR revert** (if Stage 1 insufficient):
- `git revert <merge-commit>` reverses worker.py + persistence.py + exit_monitor.py + bot_status.py + config.py + frontend changes.
- `alembic downgrade -1` reverses the schema migration (drops `timeframe` column from `shadow_cooldowns` + `shadow_open_positions`; restores old PK / UNIQUE constraints; loses any 15m rows recorded post-deploy).
- Downgrade tested per FU-10 + PR3's own `test_pr3_migration_downgrade_round_trip`.

## 9. Exit criteria (PR3 ships when)

1. ✅ All CI green (backend, frontend, docker-compose-smoke).
2. ✅ mypy clean (404+ source files).
3. ✅ ruff clean.
4. ✅ All new unit + integration tests pass.
5. ✅ `test_pr3_migration.py` passes — schema introspection confirms PK + UNIQUE changes.
6. ✅ `test_pr3_migration_downgrade_round_trip` passes (PR3-local FU-10 anticipation).
7. ✅ `test_audit_replay_identity.py` (from PR1) still passes — no regression in hash-chain replay.
8. ✅ V-7 bench `bench_shadow_handle_candle.py` passes: `delta_p50 ≤ 50ms`, `delta_p99 ≤ 200ms`.
9. ✅ Heartbeat regression test passes — `shadow_worker` heartbeats appear in `worker_heartbeats` within 30s of startup.
10. ✅ Manual operator review of full diff.
11. ✅ **5+ day staging soak** with `SHADOW_TIMEFRAMES=["1h", "15m"]` enabled.
12. ✅ **Shadow stats during soak**:
    - Combined trades (1h-only since 15m is promotion-excluded by default) accrue at expected 1h rate (no regression).
    - `per_timeframe.15m.trades_total` ≥ 50 within 5 days (proves the 15m lane is producing signals).
    - `per_timeframe.15m.win_rate` measurable — operator decides whether to flip `SHADOW_15M_ELIGIBLE_FOR_PROMOTION=True` in a follow-up.
13. ✅ Operator free-text "ship it" for dev → main merge (per `dev-prod-branch-workflow` memory).

## 10. References

- Parent: [master rollout plan — Option D](2026-05-17-master-rollout-plan-option-d.md)
- Predecessor spec: [PR2 MTF gate + SHORT safety](2026-05-17-pr2-mtf-gate-and-short-safety-design.md)
- PR1 spec: [PR1 record-only foundation](2026-05-16-pr1-record-only-design.md)
- KNOWN_ISSUES: `backend/docs/KNOWN_ISSUES.md` (FU-1 partial close, FU-10 PR3-local anticipation)
- Architecture: `docs/ARCHITECTURE.md`
- MEMORY entries: `complete-modules-before-merge`, `dispatcher-outbound-telegram-was-unwired`, `shadow-entry-thresholds`, `worker-watchdog-system`, `binance-futures-ws-geoblock`
- Hook points (PR2 code post-merge):
  - [`backend/app/shadow/worker.py`](backend/app/shadow/worker.py) — main loop, `SHADOW_TIMEFRAME` constant (line 59), `_handle_candle`, `setup()`, `_build_default_worker()` (line 435+)
  - [`backend/app/shadow/engine.py`](backend/app/shadow/engine.py) — `LONG_THRESHOLD`, `SHORT_THRESHOLD`, `PositionGate.is_blocked` (line 146+)
  - [`backend/app/shadow/persistence.py`](backend/app/shadow/persistence.py) — `set_cooldown`, `persist_closed_trade`
  - [`backend/app/shadow/exit_monitor.py`](backend/app/shadow/exit_monitor.py) — `TIMEOUT_BARS=24`
  - [`backend/app/shadow/universe.py`](backend/app/shadow/universe.py) — `load_current_universe`
  - [`backend/app/shadow/multi_stream.py`](backend/app/shadow/multi_stream.py) — `MultiStreamReader` (already parametric on `timeframe`)
  - [`backend/app/ops/worker_registry.py`](backend/app/ops/worker_registry.py) — `shadow_worker` entry (lines 68-76)
  - [`backend/app/config.py`](backend/app/config.py) — Settings model
  - [`backend/app/core/scoring/mtf_confluence.py`](backend/app/core/scoring/mtf_confluence.py) — `_KLINE_CACHE` (line 90), `_cache_get`, `prewarm_cache` — reuse target
  - [`backend/app/api/routes/bot_status.py`](backend/app/api/routes/bot_status.py) — `/promotion-gate` (line 212+), `/per-asset-stats` (line 368+)
  - [`backend/app/main.py`](backend/app/main.py) — `start_shadow_worker()` spawn (line 143), teardown (line 378)
  - [`frontend/src/tabs/BotStatus/OpenPositions.tsx`](frontend/src/tabs/BotStatus/OpenPositions.tsx) — hardcoded `"1h"` deep link (lines 36-38)
- Migration patterns (mirror PR1):
  - [`backend/alembic/versions/2026_05_17_0020_pr1_record_only_columns.py`](backend/alembic/versions/2026_05_17_0020_pr1_record_only_columns.py) — 3-step add/backfill/flip pattern
  - [`backend/alembic/versions/2026_05_04_0005_user_id_columns.py`](backend/alembic/versions/2026_05_04_0005_user_id_columns.py) — PK-extension via raw SQL pattern
