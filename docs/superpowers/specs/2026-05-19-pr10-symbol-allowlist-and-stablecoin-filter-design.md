# PR10 — Symbol allowlist + stablecoin filter

**Status**: Design draft 2026-05-19. Awaiting operator review.
**Owner**: Backend (dispatcher pre-condition + snapshot worker + alembic + persistence + tests).
**Parent**: [Strategic replan 2026-05-19](../specs/2026-05-19-strategic-replan-post-launch.md) (if filed; otherwise reference the in-thread `PART G` priority order).
**Predecessor**: Option D rollout complete (PR1-3 + PR8-9 shipped 2026-05-18).
**Behavior change**: NO at deploy time. Default-OFF via `SYMBOL_ALLOWLIST_ENABLED=False`. When operator flips ON: dispatcher skips signals on symbols that have NOT shown positive rolling Sharpe (and hard-skips stablecoin pairs regardless of flag, since stablecoin filter is part of the allowlist gate path).

---

## 1. Goal

Shadow-trade stats show **bimodal** performance: a handful of winners (TONUSDT, EDENUSDT, XRPUSDT, SUIUSDT — positive Sharpe) and many losers (FDUSDUSDT, TRXUSDT, BNBUSDT — negative Sharpe), with several **stablecoin pairs** (USDC, FDUSD, USD1, BUSD, TUSD, DAI as base) that don't move and exist as pure fee burn. Aggregate stats hide the skill split: win rate 23%, Sharpe -5.55, profit factor 0.62.

PR10 introduces an **automatic per-symbol allowlist** driven by rolling Sharpe + a trade-count grace window for new symbols. Hard-excludes stablecoin pairs from the dispatcher (not from shadow trading — shadow keeps accumulating data on all symbols so re-admission is data-driven, not memory-bound).

PR10 also does **NOT** change:
- Shadow worker's universe membership (`SHADOW_NARROW_UNIVERSE` config remains the operator-level knob; shadow keeps trading the full operator-chosen universe so per-symbol stats keep accruing on every symbol).
- Entry thresholds, scoring math, MTF compute, sizing math (PR2/3/8/9 are unchanged).
- Live trading paths beyond adding one pre-condition gate (cheap, cached, default-OFF).

---

## 2. Scope (in PR10)

| ID | Feature | What lands |
|---|---|---|
| A1 | `symbol_performance_snapshots` table | Append-only DB table, hash-chained (joins the 7 existing chained tables → 8 total). One row per (symbol, snapshot_run). Stores `trades_count`, `win_rate`, `sharpe`, `allowed`, `window_start`, `window_end`, `computed_at`. |
| A2 | `compute_per_symbol_stats` helper | Pure-function aggregator over `shadow_trades`: groups closed trades per symbol over rolling window (last 100 closed trades OR last 30 days, whichever shorter), returns trades_count + win_rate + Sharpe per symbol. Reuses existing `compute_sharpe_annualized` from `app/shadow/stats.py`. |
| A3 | `is_symbol_allowed` helper | Pure-function rule: given a snapshot, return `True` if (a) `trades_count < 50` (grace window) OR (b) `sharpe > 0`. Otherwise `False`. |
| A4 | `is_stablecoin_pair` helper | Returns True if base asset of `symbol` is in `SHADOW_STABLECOIN_EXCLUDE_LIST`. Handles symbol forms `"BTCUSDT"` (no slash) AND `"BTC/USDT"` (with slash) AND TF-prefixed forms. |
| A5 | `symbol_allowlist_refresh` worker | Daily background task. Runs `compute_per_symbol_stats` over current shadow data, writes one snapshot row per symbol (via audit-chained insert), logs summary. Registered in `worker_registry.py` with `max_staleness_seconds=2 * 86400` (2-day budget — allows one missed day before alarming). Heartbeats per run. `pending_heartbeat=False`. |
| A6 | `_apply_symbol_allowlist_gate` dispatcher pre-condition | New gate. Inserts FIRST in pre-conditions block (cheapest check — single in-memory dict lookup after cache fill). Returns `None` (no-op) when `SYMBOL_ALLOWLIST_ENABLED=False` — stablecoin check is ALSO inside this branch (no behavior change at deploy; see §3.5). When flag is True: emits `DispatchResult(outcome="blocked_symbol_excluded")` with `detail="stablecoin_excluded"` for stablecoin pairs OR `detail="low_sharpe"` for sharpe-rejected symbols (single outcome literal, detail string distinguishes — see §8 #9). |
| A7 | In-memory allowlist cache | Process-local; TTL 1h. Keyed on `user_id`. Single shared dict; rebuilt on first dispatch after expiry. Cache miss costs one query over `symbol_performance_snapshots`. Fail-open on query error. |
| A8 | `DispatchOutcome` enum entry | New literal `"blocked_symbol_excluded"` added to dispatcher's `DispatchOutcome` type. |
| A9 | `SHADOW_STABLECOIN_EXCLUDE_LIST` config | `list[str]` in `app/config.py`. Default: `["USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI"]`. Base-asset names (no quote suffix). Env-overridable. |
| A10 | `SYMBOL_ALLOWLIST_ENABLED` config | `bool` in `app/config.py`. Default `False`. When `False`, the dispatcher gate function returns `None` immediately — zero behavior change at deploy. Stablecoin filter is part of the same gate function; flag-OFF also skips the stablecoin check. (Rationale below in §3.5.) |
| A11 | `/api/v1/bot-status/symbol-allowlist` endpoint | Read-only GET. Returns list of latest-per-symbol snapshots with `symbol`, `trades_count`, `sharpe`, `win_rate`, `allowed`, `computed_at`. Sorted by `sharpe DESC`. Auth via existing `current_user_or_impersonated`. Default-OFF doesn't gate the endpoint itself — operator can inspect the allowlist before flipping the flag. |
| A12 | `SymbolAllowlistOut` Pydantic schema | One per snapshot row + 1 `last_refresh_at` overall. |
| BENCH | Dispatcher pre-conditions latency bench | `bench_dispatcher_preconditions.py` (existing from PR8) extended with `--allowlist=on/off` flag. V-7 budget: Δp50 ≤ 2ms (cache hit) / ≤ 10ms (cache miss), Δp99 ≤ 10ms (cache hit). |
| DEFERRED | TOTP-gated manual override | Per Part H text. Out of PR10 — operator can shrink the active universe via `SHADOW_NARROW_UNIVERSE` config until then. Future PR10.5. |
| DEFERRED | Auto re-admission of excluded symbols | A symbol once excluded stays excluded until either (a) more shadow trades land (which they will if the symbol is in `SHADOW_NARROW_UNIVERSE`) and (b) Sharpe goes positive in the rolling window. Re-admission is automatic via the daily snapshot — but no special "probation" path. PR10.5 if observed insufficient. |
| DEFERRED | Per-direction (LONG/SHORT) allowlists | Tier 2. PR10 aggregates LONG + SHORT into one symbol-level Sharpe. |
| DEFERRED | UI rendering of `/symbol-allowlist` | Backend ships the endpoint; frontend Bot Status tab can read it in a future PR. PR10 deploys with operator-curl-only inspection. |

---

## 3. Architecture

### 3.1 Data flow

```
SHADOW WORKER                               DISPATCHER (live + telegram-approve)
─────────────                               ──────────────────────────────────
new closed trade → shadow_trades            SignalProposal arrives
                                                  │
DAILY (symbol_allowlist_refresh worker)           ▼
       │                                    pre-conditions:
       ▼                                      ┌─ symbol_allowlist_gate  ← NEW (cheapest, first)
SELECT closed shadow_trades per symbol         │    if not enabled: None
SELECT min(100 most-recent, last 30d)          │    if stablecoin:   blocked_symbol_excluded
COMPUTE win_rate, sharpe per symbol            │    if not allowed:  blocked_symbol_excluded
INSERT symbol_performance_snapshots row        ├─ funding-rate guard
       │  (hash-chained)                       ├─ cooldown gate (PR8)
       ▼                                       ├─ MTF gate (PR2)
record_heartbeat(symbol_allowlist_refresh)     ├─ SHORT safety (PR2)
                                               └─ max concurrent positions
                                                  │
                                                  ▼
                                            position sizing + place_order
```

Snapshot table is read by the dispatcher pre-condition; written by the daily worker. Cache TTL 1h shorts the path on contended hot path.

### 3.2 Allowlist decision rule

```python
def is_symbol_allowed(snapshot: SymbolSnapshot, *, grace_trades: int = 50) -> bool:
    """Allowlist inclusion rule.

    A symbol is allowed if either:
      - It has < grace_trades closed trades (new-symbol grace), OR
      - Its rolling Sharpe is strictly positive.

    Negative Sharpe with >= grace_trades closed trades → excluded.
    """
    if snapshot.trades_count < grace_trades:
        return True
    return (snapshot.sharpe or 0.0) > 0.0
```

**Tie-breaking on `sharpe is None`:** `compute_sharpe_annualized` returns `None` when there's insufficient data (typically < 2 trades). The rule treats `None` as 0 (so a new-but-not-yet-graced symbol stays excluded if it somehow has trades but no Sharpe). In practice this branch is rare; the grace check covers the typical case.

### 3.3 Stablecoin pair detection

```python
_STABLECOIN_BASES_DEFAULT: list[str] = [
    "USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI",
]

def _parse_base_asset(symbol: str) -> str:
    """Extract base from BTC/USDT, BTCUSDT, or BTC-USDT forms.

    Strips known quote suffixes (USDT/USDC/BUSD/USD) longest-first.
    Falls back to entire string if no recognized quote pattern.
    """
    s = symbol.replace("/", "").replace("-", "").upper()
    for quote in ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "USD"):
        if s.endswith(quote) and len(s) > len(quote):
            return s[:-len(quote)]
    return s

def is_stablecoin_pair(symbol: str, settings) -> bool:
    base = _parse_base_asset(symbol)
    return base in {b.upper() for b in settings.SHADOW_STABLECOIN_EXCLUDE_LIST}
```

**Quote-suffix ordering matters** — `FDUSD` is a base name AND a quote name; the loop checks longest-first so `FDUSD/USDT` correctly extracts base `FDUSD` (length 5 quote stripped first). The function is symmetric: `FDUSDUSDT` → strip `USDT` (len 4) first → base `FDUSD` → matched as stablecoin. ✓

### 3.4 Cache shape + invalidation

```python
@dataclass
class _AllowlistCache:
    snapshot_map: dict[str, SymbolSnapshot]   # symbol → latest snapshot
    last_refresh: datetime                     # when this was built
    ttl: timedelta = timedelta(hours=1)

    def is_fresh(self, now: datetime) -> bool:
        return (now - self.last_refresh) < self.ttl
```

Single process-local instance keyed on `user_id`. First dispatch after process start (or after TTL expiry) takes one DB read; subsequent dispatches hit the dict. Daily worker DOES NOT invalidate caches — the 1h TTL is sufficient since snapshots are written once per day.

**Concurrent dispatch warming:** the cache is rebuilt under a per-user `asyncio.Lock`. First-arriver pays the DB cost; siblings await.

### 3.5 Default-OFF rationale

Operator chose **dispatcher-only stablecoin filter** (not shadow-universe-level) so PR10 has **zero behavior change at deploy**. Implementation: the entire gate function returns `None` immediately when `SYMBOL_ALLOWLIST_ENABLED=False`:

```python
async def _apply_symbol_allowlist_gate(*, proposal, user_id, session, settings, ...):
    if not settings.SYMBOL_ALLOWLIST_ENABLED:
        return None
    # Stablecoin check ALSO inside this branch — operator opts into the whole filter.
    if is_stablecoin_pair(proposal.symbol, settings):
        return DispatchResult(outcome="blocked_symbol_excluded", detail="stablecoin_excluded")
    # ... allowlist check
```

When the operator flips ON: BOTH the allowlist gate AND the stablecoin filter activate together. This is a single behavior switch. Operator who wants to keep trading stablecoins (unlikely) keeps the flag off.

### 3.6 Fail-open contract

The gate fails open on any DB error (load_latest_snapshots() raises) or compute error (is_symbol_allowed() raises). Mirrors PR2's MTF gate + PR8's cooldown gate philosophy — a stuck filter that errored to-blocked could shut down trading for legitimate signals.

Test: `test_apply_symbol_allowlist_gate_fails_open_on_db_error`. The error is logged at WARNING; trade proceeds.

---

## 4. File structure

### 4.1 Created

| Path | Responsibility |
|---|---|
| `backend/alembic/versions/2026_05_19_0024_pr10_symbol_performance_snapshots.py` | Migration: `symbol_performance_snapshots` table + chained-table registration. |
| `backend/app/trading/symbol_allowlist.py` | Pure functions: `_parse_base_asset`, `is_stablecoin_pair`, `compute_per_symbol_stats`, `is_symbol_allowed`, plus the `_AllowlistCache` dataclass + load-with-cache helper. |
| `backend/app/trading/execution/symbol_allowlist_gate.py` | `_apply_symbol_allowlist_gate(proposal, user_id, session, settings, cache) -> DispatchResult \| None`. Mirrors `app/trading/execution/cooldown_gate.py` (PR8). |
| `backend/app/db/symbol_performance_snapshots.py` | Persistence: `load_latest_snapshots_per_symbol(session)`, `insert_snapshot_row(session, ...)` (via `insert_with_chain`), plus the `SymbolSnapshot` dataclass. |
| `backend/app/workers/symbol_allowlist_refresh.py` | Daily worker loop: list of symbols → for each: compute_per_symbol_stats → insert_snapshot_row → log summary. Heartbeats per cycle. |
| `backend/tests/db/test_pr10_migration.py` | Postgres introspection: table, PK, chained-table registration, downgrade round-trip. |
| `backend/tests/db/test_pr10_migration_downgrade.py` | Round-trip upgrade → downgrade → upgrade → head. |
| `backend/tests/unit/test_pr10_settings_defaults.py` | All settings defaults. |
| `backend/tests/unit/test_symbol_allowlist.py` | Pure-function tests: `_parse_base_asset`, `is_stablecoin_pair`, `is_symbol_allowed`, `_AllowlistCache.is_fresh`. |
| `backend/tests/unit/test_compute_per_symbol_stats.py` | Aggregation logic: per-symbol grouping, Sharpe per symbol, grace-window, rolling-window edge cases. |
| `backend/tests/db/test_symbol_performance_snapshots_persistence.py` | Round-trip insert + load. |
| `backend/tests/trading/test_symbol_allowlist_gate.py` | Dispatcher integration: enabled/disabled/stablecoin/excluded/included/fail-open. |
| `backend/tests/integration/test_pr10_dispatcher_e2e.py` | E2E: signal on excluded symbol → blocked_symbol_excluded; signal on allowed → proceeds. |
| `backend/tests/integration/test_pr10_allowlist_endpoint.py` | `/symbol-allowlist` response shape + auth + empty-DB fallback. |
| `backend/tests/workers/test_symbol_allowlist_refresh.py` | Worker writes one snapshot row per symbol; idempotent on re-run within same UTC day; heartbeat fires. |
| `backend/scripts/bench_dispatcher_allowlist.py` | V-7 microbench (gate disabled / cache hit / cache miss). Same shape as PR8's bench. |

### 4.2 Modified

| Path | Reason |
|---|---|
| `backend/app/config.py` | Add `SYMBOL_ALLOWLIST_ENABLED=False`, `SHADOW_STABLECOIN_EXCLUDE_LIST=[...]`, `SYMBOL_ALLOWLIST_GRACE_TRADES=50`, `SYMBOL_ALLOWLIST_WINDOW_TRADES=100`, `SYMBOL_ALLOWLIST_WINDOW_DAYS=30`, `SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS=3600`. |
| `backend/app/trading/execution/dispatcher.py` | Wire `_apply_symbol_allowlist_gate` FIRST in pre-conditions block. Add `"blocked_symbol_excluded"` literal to `DispatchOutcome` Union. |
| `backend/app/db/audit.py` | Add `"symbol_performance_snapshots": frozenset({...})` entry to `HASH_PAYLOAD_COLUMNS`. |
| `backend/app/api/routes/bot_status.py` | New `/symbol-allowlist` endpoint. |
| `backend/app/api/schemas.py` | `SymbolAllowlistOut` + `SymbolAllowlistResponseOut` schemas. |
| `backend/app/ops/worker_registry.py` | Register `symbol_allowlist_refresh` (2-day staleness budget). |
| `backend/app/main.py` | Spawn `symbol_allowlist_refresh` task in lifespan. Spawns unconditionally — runs in all modes (manual/telegram-approve/fully-auto). The snapshot data is useful regardless of dispatcher gate state. |
| `backend/tests/unit/test_worker_registry_consistency.py` | New worker entry. |
| `backend/tests/db/test_pr8_migration_downgrade.py`, `test_pr3_migration_downgrade.py`, etc. | Already updated in PR8 sweep to always leave DB at `alembic upgrade head`; PR10 inherits this hygiene. |
| `docs/ARCHITECTURE.md` | New §12 — Symbol allowlist + stablecoin filter (per-symbol Sharpe gate, daily snapshot worker, dispatcher pre-condition). |
| `docs/superpowers/specs/2026-05-17-master-rollout-plan-option-d.md` | Add PR10 to the strategic-replan tracking section (already covered in operator's in-thread Part G; codify in master doc). |

---

## 5. Settings (new)

```python
# --- PR10 symbol allowlist + stablecoin filter --------------------------
# Default-OFF for safe deploy. When False, dispatcher gate short-circuits
# without DB read; bit-identical to pre-PR10 dispatch behavior. Stablecoin
# filter is part of the same gate function — also dormant under flag=False.
SYMBOL_ALLOWLIST_ENABLED: bool = False

# Quote-stripped base asset names to exclude from real-money dispatch
# regardless of Sharpe stats. Shadow trading on these symbols continues
# (controlled by SHADOW_NARROW_UNIVERSE).
SHADOW_STABLECOIN_EXCLUDE_LIST: list[str] = [
    "USDC", "FDUSD", "USD1", "BUSD", "TUSD", "DAI",
]

# A symbol with < this many closed trades is in the "grace window" and
# is allowlisted regardless of Sharpe. Prevents excluding new symbols
# before they have meaningful data.
SYMBOL_ALLOWLIST_GRACE_TRADES: int = 50

# Rolling window for Sharpe computation. Sharpe is computed over
# min(SYMBOL_ALLOWLIST_WINDOW_TRADES most-recent closed,
#     trades in last SYMBOL_ALLOWLIST_WINDOW_DAYS days) — whichever is
# the smaller set. Smooths volatile recent data while staying responsive.
SYMBOL_ALLOWLIST_WINDOW_TRADES: int = 100
SYMBOL_ALLOWLIST_WINDOW_DAYS: int = 30

# In-memory allowlist cache TTL. After expiry, next dispatch re-queries
# the latest snapshot per symbol. 1h is comfortably faster than the
# daily snapshot refresh, so cache rebuild reads fresh data.
SYMBOL_ALLOWLIST_CACHE_TTL_SECONDS: int = 3600
```

---

## 6. Schema

### 6.1 `symbol_performance_snapshots` (new, hash-chained — 8th chained table)

```sql
CREATE TABLE symbol_performance_snapshots (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    window_start TIMESTAMP WITH TIME ZONE NOT NULL,
    window_end TIMESTAMP WITH TIME ZONE NOT NULL,
    trades_count INTEGER NOT NULL CHECK (trades_count >= 0),
    win_rate REAL,          -- 0.0 .. 1.0; NULL if trades_count == 0
    sharpe REAL,            -- annualized; NULL if trades_count < 2
    allowed BOOLEAN NOT NULL,  -- decision at snapshot time (post-rule)
    computed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    -- audit chain
    prev_hash TEXT NOT NULL,
    row_hash TEXT NOT NULL UNIQUE,
    inputs_hash TEXT
);
CREATE INDEX ix_symbol_perf_symbol_computed
    ON symbol_performance_snapshots (symbol, computed_at DESC);
```

Index optimizes the "latest snapshot per symbol" read path used by the dispatcher gate (and the `/symbol-allowlist` endpoint).

`HASH_PAYLOAD_COLUMNS["symbol_performance_snapshots"]` covers `{symbol, window_start, window_end, trades_count, win_rate, sharpe, allowed, computed_at}` — every column except `id`, `prev_hash`, `row_hash`, `inputs_hash`. (`inputs_hash` is allow-listed in `NON_HASHED_ALLOW_LIST` per audit chain convention.)

### 6.2 Migration plan (1-step)

Single forward CREATE TABLE + CREATE INDEX. Reversible downgrade: `DROP TABLE symbol_performance_snapshots`. The audit chain HASH_PAYLOAD_COLUMNS addition is a code-level change synced with the migration's deploy.

---

## 7. Test surface

**~30 cases across ~10 files:**

- Migration introspection + downgrade round-trip (4)
- Settings defaults (6)
- `_parse_base_asset` (5: BTC/USDT, BTCUSDT, FDUSD/USDT, FDUSDUSDT, edge cases)
- `is_stablecoin_pair` (4: known stablecoins, non-stablecoin, env override, case-insensitivity)
- `is_symbol_allowed` matrix (4: grace-window, positive Sharpe, negative Sharpe, None Sharpe)
- `compute_per_symbol_stats` (4: per-symbol grouping, rolling-window cutoff, empty input, Sharpe-None propagation)
- `_AllowlistCache.is_fresh` (2: within TTL, expired)
- Persistence round-trip (3: insert chained row, load latest per symbol, multi-snapshot ordering)
- Dispatcher gate matrix (6: flag-disabled, no-snapshot fallback, stablecoin, allowed, excluded-by-sharpe, fail-open on db-error)
- Worker (3: writes 1 row per symbol, idempotent same-UTC-day, heartbeat fires)
- `/symbol-allowlist` endpoint (3: empty DB → empty list, populated → sorted by Sharpe desc, auth gate)
- Bench V-7 (gate-disabled / cache-hit / cache-miss; budget Δp50 ≤ 2ms hit, ≤ 10ms miss)

---

## 8. Operator decision points

Items below are choices I made unilaterally; operator can redirect each before plan-write.

1. **Grace-window threshold = 50 trades.** Operator's text said "new symbols get 50 trades to prove themselves." Confirmed.
2. **Sharpe threshold = strictly > 0.** Even marginal-positive symbols pass. Operator's text said "include if Sharpe > 0". Confirmed.
3. **Rolling window = min(100 trades, 30 days), whichever is smaller.** Operator's text said "window: last 100 closed trades, OR last 30 days, whichever is shorter." Confirmed.
4. **Daily snapshot worker (vs weekly).** Operator confirmed in mid-brainstorm reply.
5. **Dispatcher-only stablecoin filter** (vs shadow-universe-level). Operator confirmed.
6. **Manual override deferred to PR10.5.** Operator's Part H listed this as IN scope; deferring keeps PR10 tight. If operator wants it in PR10, easy to add as A13 (TOTP endpoint that writes a `manual_allowlist_overrides` table; cache layer consults this before snapshot data). Defaulted to defer; flag.
7. **Worker spawns unconditionally** (not gated by `AUTONOMOUS_TRADING_ENABLED`). Snapshot data is useful regardless of dispatcher state — `/symbol-allowlist` endpoint surfaces the allowlist for inspection even in manual mode.
8. **Symbol form normalization** in `_parse_base_asset` handles `BTC/USDT`, `BTCUSDT`, `BTC-USDT`. Quote-suffix priority is longest-first (FDUSD before USDC before USDT before USD).
9. **`blocked_symbol_excluded` outcome** (single literal for both stablecoin + Sharpe rejection). Distinguishable in logs via the `detail` field. Operator may prefer split literals (`blocked_stablecoin` + `blocked_low_sharpe`); flag.

---

## 9. Risk register

| Risk | Severity | Mitigation |
|---|---|---|
| Allowlist excludes a winner due to short-term streak | MEDIUM | 100-trade window smooths; grace-window protects new symbols; weekly→daily snapshot cadence catches re-emergence within 1 day of streak ending |
| Snapshot worker fails silently | MEDIUM | Heartbeat fires per cycle; watchdog alerts after 2-day budget (2× normal cadence) |
| Cache stale across dispatcher restarts | LOW | TTL 1h; first dispatch after restart pays one query; restart is rare event |
| Symbol form mismatch (BTC/USDT vs BTCUSDT) excludes wrong symbol | MEDIUM | `_parse_base_asset` test cases cover both forms; gate test verifies both forms produce same allowlist decision |
| FU-24 (insert_with_chain race) corrupts new chained table | LOW for this table | Snapshot worker is single-threaded, writes once per symbol per day; no concurrent inserts → race doesn't fire |
| `compute_per_symbol_stats` query on growing `shadow_trades` slows over time | LOW | Bounded by rolling window (100 trades or 30 days); query is indexed on `(closed_at, symbol)` if index exists, else worth adding |
| Operator flips flag with no snapshot data yet | LOW | First dispatch finds empty `snapshot_map`; rule defaults to "allow all" (no row in snapshot table → not in dict → not excluded). Defensive `if symbol not in snapshot_map: return True` in gate. Worker writes within 24h. |
| Allowlist endpoint exposes per-user data via impersonation | LOW | Existing `current_user_or_impersonated` auth pattern; endpoint returns the impersonated user's stats correctly |

---

## 10. Out of scope (deferred)

- **TOTP-gated manual override** — PR10.5.
- **Per-direction (LONG/SHORT) allowlists** — Tier 2 (T2 PR).
- **ML-based symbol selection** — Tier 3 (post-Phase B brain work).
- **Auto re-admission special path** — automatic via daily snapshot already covers; no special "probation" needed.
- **UI rendering of `/symbol-allowlist`** — backend endpoint ships; frontend Bot Status tab integration is a separate small PR.
- **Per-tier Sharpe thresholds** (e.g., "small accounts need higher Sharpe") — Tier 3.
- **Time-of-day filtering** (e.g., "only trade during high-liquidity hours") — Tier 2 (T2.6 news blackout windows is related; deferred to PR18).

---

## 11. Acceptance criteria

PR10 ships when **all** of these hold:

- [ ] All ~30 tests pass; lint + mypy clean.
- [ ] V-7 bench passes (Δp50 ≤ 2ms cache-hit, Δp99 ≤ 10ms cache-miss).
- [ ] Migration applies cleanly on staging Postgres + reversible downgrade tested.
- [ ] Default-OFF in prod (`SYMBOL_ALLOWLIST_ENABLED=False`) — no change to existing trade flow at deploy time. Bit-identical dispatcher hot path.
- [ ] After flipping ON in staging: `/symbol-allowlist` endpoint returns sane data; one trading day of dispatch logs show `blocked_symbol_excluded` for FDUSD/USD1/TRX (stablecoins + losing symbols).
- [ ] Audit chain replay-identity verifies for the new `symbol_performance_snapshots` table.
- [ ] ARCHITECTURE.md §12 published; master rollout doc updated.

---

**End of design draft.** Operator: review §8 decision points (esp. #6 manual override defer, #9 split-literal decision); redirect if any defaults need to change before plan-write.
