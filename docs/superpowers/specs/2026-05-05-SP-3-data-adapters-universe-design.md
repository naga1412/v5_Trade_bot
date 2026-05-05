# SP-3 — Data Adapters + Universe Design Spec

**Date:** 2026-05-05
**Status:** Approved (autonomous-mode default; user can redirect)
**Implementation target:** Sub-project SP-3 (parallel-safe with SP-2; can ship after SP-2 or in parallel branch)
**Depends on:** SP-0 (Binance adapter exists), SP-0.7 (admin REST surface for adapter health endpoint)
**Companion specs:** `2026-05-01-trading-radar-meta-plan-design.md` §5.2 (point-in-time universe), §5.15 (rate-limit accounting)

---

## 1. Purpose

Add **3 new exchange adapters** (Bybit, Yahoo Finance via yfinance, TwelveData) to complement the existing **Binance** adapter, plus a **point-in-time `universe_history` table** that records when each symbol was listed/delisted on each exchange. Wire a **Redis-backed rate-limited client** that enforces each exchange's free-tier quotas. Add a **cross-exchange symbol mapper** so the same logical asset (e.g., BTC/USDT) can be looked up on any adapter.

After SP-3, the bot can:
- Pull OHLCV from 4 different sources (cross-validation, fallback)
- Filter backtests by point-in-time tradability (no survivorship bias)
- Stay within each exchange's free-tier limits without manual throttling
- Pull non-crypto data (Yahoo) for macro context (DXY, gold, S&P 500 if useful for L1 macro layer)

### Non-goals

- **No order placement.** SP-3 is read-only data — order execution is SP-8 (autonomous trading).
- **No new indicator computations.** Adapters produce raw OHLCV; indicators come from SP-2.
- **No on-chain data adapters** (Glassnode, etc.) — deferred to SP-3.5 if needed.
- **No exchange-specific arbitrage features** (cross-exchange spreads, etc.) — SP-5 territory.
- **No historical bulk-import scheduler.** Each adapter ships with a manual `tools/data/bulk_import_<exchange>.py` script; cron scheduling is SP-7.
- **No paid-tier upgrades.** Stay within free quotas; if a bot can't survive within free quotas, the design itself is wrong.

---

## 2. Locked decisions

| # | Decision | Value |
|---|---|---|
| 1 | Crypto exchange #1 | **Binance** — already exists from SP-0.5; SP-3 hardens it with shared rate-limit middleware |
| 2 | Crypto exchange #2 | **Bybit** — second-largest derivatives venue; via `bybit-pybit` library or direct REST |
| 3 | Stocks/macro #1 | **Yahoo Finance** via `yfinance` — DXY, gold, S&P 500, individual equities (free, unofficial throttle) |
| 4 | Macro #2 | **TwelveData** — free tier 800 calls/day; macro indicators not in Yahoo (e.g., real-time fed funds, currency crosses) |
| 5 | Adapter interface | All adapters implement the `ExchangeAdapter` Protocol with `fetch_klines(symbol, tf, limit, start, end) → list[Candle]` and `list_symbols() → list[SymbolInfo]` |
| 6 | Symbol normalization | Internal canonical form: `"BTC/USDT"` (slash separator, uppercase). Each adapter has `to_native(canonical: str) → str` and `from_native(native: str) → str` |
| 7 | Rate limiting | **Redis token-bucket** (`RateLimitedClient` middleware in `app/data/ratelimit.py`) — already exists from SP-0; extended to support per-endpoint weights for Binance |
| 8 | Universe table | New table `universe_history(id, exchange, symbol, listed_at, delisted_at)` — populated by per-exchange `sync_universe()` worker |
| 9 | Universe sync cadence | Daily at 02:00 UTC (offset from shadow trading universe refresh which is 00:00 UTC) |
| 10 | `is_tradable(symbol, ts)` | Returns `True` only if a `universe_history` row exists with `listed_at <= ts < (delisted_at OR +infinity)` for ANY exchange |
| 11 | Adapter failure tolerance | Each adapter is independent — Bybit being down doesn't affect Binance/Yahoo data flow |
| 12 | Test coverage | Each adapter has ≥10 tests covering: happy path, empty response, malformed JSON, rate-limit retry, network timeout |
| 13 | Library pins | `pybit==5.7.0` (Bybit), `yfinance==0.2.40` (Yahoo), TwelveData via direct httpx (no library) |

---

## 3. Architecture

### 3.1 Module layout

```
backend/app/data/
├── adapters/
│   ├── __init__.py             — registry + factory
│   ├── binance.py              ✓ exists (SP-0.5) — extend with shared rate-limit middleware
│   ├── bybit.py                NEW
│   ├── yahoo.py                NEW
│   ├── twelvedata.py           NEW
│   └── _base.py                NEW — ExchangeAdapter Protocol + Candle dataclass + SymbolInfo
├── ratelimit.py                ✓ exists; extend per-exchange config
├── universe.py                 ✓ exists; refactor to use universe_history table
├── universe_sync.py            NEW — daily sync worker
└── symbols.py                  NEW — cross-exchange symbol normalization
```

### 3.2 ExchangeAdapter protocol

```python
from typing import Protocol, Literal
from datetime import datetime
from dataclasses import dataclass

@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class SymbolInfo:
    canonical: str        # "BTC/USDT"
    native: str           # exchange-specific: "BTCUSDT" on Binance, "BTCUSDT" on Bybit, "BTC-USD" on Yahoo
    base: str             # "BTC"
    quote: str            # "USDT"
    listed_at: datetime | None
    delisted_at: datetime | None
    asset_class: Literal["crypto", "stock", "fx", "commodity", "index"]

class ExchangeAdapter(Protocol):
    name: str             # "binance", "bybit", "yahoo", "twelvedata"

    async def fetch_klines(
        self, *, symbol: str, timeframe: str,
        limit: int = 500, start: datetime | None = None, end: datetime | None = None,
    ) -> list[Candle]:
        ...

    async def list_symbols(self) -> list[SymbolInfo]:
        ...
```

Each adapter handles its own:
- Symbol normalization (`to_native`, `from_native`)
- Rate-limit budget (passes its limits to `RateLimitedClient`)
- Error handling (network timeouts → empty list with log warning; malformed JSON → raise)
- Pagination (some exchanges cap at 500-1000 bars per request; loop internally)

### 3.3 Rate-limited client

`app/data/ratelimit.py:RateLimitedClient` wraps `httpx.AsyncClient` with a token-bucket per `(exchange, endpoint_group)`:

```python
class RateLimitedClient:
    def __init__(
        self, exchange: str, *,
        weight_per_minute: int,
        endpoint_weights: dict[str, int] | None = None,
        redis: Redis,
    ): ...

    async def request(
        self, method: str, url: str, *,
        endpoint_key: str = "default",  # for per-endpoint weight lookup
        weight: int | None = None,       # explicit weight override
        **httpx_kwargs,
    ) -> httpx.Response:
        # 1. Check Redis token bucket for (exchange, endpoint_key)
        # 2. If exhausted: sleep until next refill OR raise RateLimitExceeded
        # 3. Subtract weight from bucket
        # 4. Make request
        # 5. Update bucket from response header (e.g., Binance X-MBX-USED-WEIGHT-1M)
```

Per-exchange config from spec §5.15:

| Exchange | Quota | Tracking key |
|---|---|---|
| Binance | weight 1200/min | `(binance, default)` — refill from `X-MBX-USED-WEIGHT-1M` |
| Bybit | 120 req/sec spot, 600 req/5sec for derivatives | `(bybit, spot)`, `(bybit, derivs)` |
| Yahoo | self-throttle 1 req/sec | `(yahoo, default)` — fixed-rate token bucket |
| TwelveData | 800 calls/day | `(twelvedata, default)` — daily counter, reset 00:00 UTC |

### 3.4 Universe sync worker

`app/data/universe_sync.py` runs once at startup + daily at 02:00 UTC:

```python
async def sync_universe(adapter: ExchangeAdapter, session: AsyncSession) -> SyncResult:
    """Pull current symbol list from exchange, diff against universe_history, INSERT/UPDATE.

    - New symbol: INSERT row with listed_at = now
    - Existing symbol still listed: leave alone
    - Symbol missing from current list (was in DB, not in API): UPDATE delisted_at = now
    - Already-delisted symbol still missing: skip
    """
```

Returns counts: `added=12, still_active=240, newly_delisted=3`.

For Yahoo: there's no "list all symbols" endpoint. Universe is whatever the user/bot configures explicitly. Skip the sync; populate `universe_history` manually via `tools/data/seed_yahoo_symbols.py`.

For TwelveData: paid `/stocks` endpoint required for full list — skip auto-sync, manual seed only.

### 3.5 Cross-exchange symbol mapper

`app/data/symbols.py`:

```python
def to_native(exchange: str, canonical: str) -> str:
    """BTC/USDT -> BTCUSDT (Binance, Bybit) or BTC-USD (Yahoo)."""
    ...

def from_native(exchange: str, native: str) -> str:
    """Inverse of to_native."""
    ...

def is_supported(exchange: str, canonical: str) -> bool:
    """Some pairs only exist on certain exchanges (e.g., DXY only on Yahoo)."""
    ...
```

Heuristics:
- Binance/Bybit: drop slash, uppercase
- Yahoo: replace `/USDT` with `-USD`, drop slash for stocks (`AAPL` stays `AAPL`)
- TwelveData: keep slash for FX (`EUR/USD`), drop for stocks

Stored in module-level dicts; per-exchange edge cases handled with explicit overrides.

---

## 4. Data model

### 4.1 New table: `universe_history`

```sql
CREATE TABLE universe_history (
    id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,                     -- 'binance', 'bybit', 'yahoo', 'twelvedata'
    symbol TEXT NOT NULL,                       -- canonical form: 'BTC/USDT', 'AAPL'
    asset_class TEXT NOT NULL,                  -- 'crypto', 'stock', 'fx', 'commodity', 'index'
    listed_at TIMESTAMPTZ NOT NULL,
    delisted_at TIMESTAMPTZ,                    -- NULL = still listed
    last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB,                             -- exchange-specific extras (base, quote, contract_size, etc.)
    UNIQUE (exchange, symbol)
);
CREATE INDEX universe_history_exchange_active_idx ON universe_history (exchange) WHERE delisted_at IS NULL;
CREATE INDEX universe_history_symbol_idx ON universe_history (symbol);
```

Migration 0010 creates this. Seeded by per-adapter `sync_universe()` runs.

### 4.2 No changes to `predictions`, `shadow_trades`, etc.

Existing tables continue to use `symbol` directly. The universe_history table is queried by `is_tradable()` and backtests, NOT joined into every query.

### 4.3 New table: `adapter_health`

```sql
CREATE TABLE adapter_health (
    id BIGSERIAL PRIMARY KEY,
    exchange TEXT NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_healthy BOOLEAN NOT NULL,
    latency_ms INTEGER,
    error_message TEXT,
    quota_used_pct DOUBLE PRECISION             -- 0.0 to 1.0; how full the rate-limit bucket is
);
CREATE INDEX adapter_health_recent_idx ON adapter_health (exchange, checked_at DESC);
```

Populated by a small background task that pings each adapter's lightweight endpoint every 5 min. Surfaces in admin UI.

---

## 5. API surface

### 5.1 Admin REST (admin-only via `Depends(require_admin)`)

```
GET  /api/v1/admin/adapters/health         — list latest health row per exchange
POST /api/v1/admin/adapters/{exchange}/sync — trigger universe sync immediately
GET  /api/v1/admin/universe?exchange=...&active=true
                                            — list symbols with optional filters
```

### 5.2 Internal API (no REST surface — used by other modules)

```python
from app.data.adapters import get_adapter
from app.data.universe import is_tradable

binance = get_adapter("binance")          # returns the registered adapter instance
candles = await binance.fetch_klines(symbol="BTC/USDT", timeframe="1h", limit=300)

if is_tradable("LUNA/USDT", datetime(2022, 6, 1)):
    # fire backtest signal at June 2022
    ...
else:
    # LUNA was delisted before this date — skip
    ...
```

### 5.3 Frontend

Minimal — just the admin Adapters health page (a sub-tab under Admin). Defer to SP-6 if time-pressed; backend admin endpoints sufficient for v1.

---

## 6. Validation procedure

1. **Unit tests:** each adapter has ≥10 tests using `respx` to mock HTTP responses
2. **Integration test:** real Binance + Bybit API ping (live network — skipped in CI unless `RUN_LIVE_API_TESTS=1`)
3. **Universe sync test:** seed `universe_history` with a synthetic snapshot, then run `sync_universe()` against a mocked adapter response, assert correct INSERT/UPDATE/delisted_at flips
4. **Rate-limit test:** call adapter in tight loop, assert `RateLimitExceeded` raised when bucket empties
5. **Manual:** verify `is_tradable("LUNA/USDT", "2022-04-01")` returns True, `is_tradable("LUNA/USDT", "2024-01-01")` returns False after seeding LUNA's delisting date

---

## 7. Sub-project sequencing

This spec is implemented as **SP-3**, parallel-safe with SP-2 (different files entirely). After SP-3 ships, the natural next sub-projects:

- **SP-5** — Full scoring + traps (depends on SP-2 patterns + SP-3 universe)
- **SP-1.1** — Train first Conv-LSTM checkpoint (orthogonal; can use SP-3's universe data for richer training set)
- **SP-3.5** — On-chain data adapters (Glassnode, etc.) — opt-in follow-up

---

## 8. Implementation cost estimate

- Sub-project size: **~25-35 tasks across 5 phases**
- Wall-clock: **~3 weeks of subagent-driven work** (per meta-plan §3 §177, with 4-subagent parallelism)
- Phase ordering:
  - **Phase A — Worktree + scaffolding + ExchangeAdapter Protocol + universe_history migration + adapter_health migration** (~5 tasks)
  - **Phase B — Binance adapter hardening + extract shared rate-limit middleware** (~3 tasks; mostly refactoring existing code)
  - **Phase C — Bybit adapter** (~5 tasks; parallel-safe with B/D/E)
  - **Phase D — Yahoo adapter** (~5 tasks; parallel-safe)
  - **Phase E — TwelveData adapter** (~4 tasks; parallel-safe)
  - **Phase F — Universe sync + symbol mapper + admin endpoints + ship** (~6 tasks)
- New backend modules: `app/data/adapters/{bybit,yahoo,twelvedata,_base}.py`, `app/data/universe_sync.py`, `app/data/symbols.py`, `app/api/routes/admin_adapters.py`
- New tests: ~50 (10 per adapter × 4 + universe sync + symbol mapper + admin)
- Database migrations: 1 (0010 — universe_history + adapter_health)

---

## 9. Cross-cutting policy compliance

| Policy | How SP-3 satisfies it |
|---|---|
| §5.2 survivorship bias | `universe_history` table + `is_tradable(symbol, ts)` enforces point-in-time filter |
| §5.15 rate limits | `RateLimitedClient` per exchange with quotas from spec §5.15 |
| §2.6 Cloudflare Access | New admin endpoints inherit `Depends(require_admin)` from SP-0.7 |
| §5.14 audit chain | `universe_history` is NOT chained (it's source-of-truth from external APIs); `adapter_health` is append-only stats; neither needs hash chain |
| §5.8 WebSocket reliability | Adapters expose REST + WebSocket where supported; existing WS reconnect logic from SP-0 covers Binance; Bybit adapter adds its own WS reconnect (out of scope here — SP-3.5 if needed) |

---

## 10. Risk + fallback plan

| Failure mode | Detection | Fallback |
|---|---|---|
| Bybit `pybit==5.7.0` API drift | Unit tests fail on next library update | Pin and document; manual re-validate on bump |
| Yahoo unofficial throttle changes | Live integration test starts returning 429 | Increase self-throttle to 1 req/2s |
| TwelveData free tier quota exhausted mid-day | Daily counter alerts | Queue requests, retry at 00:00 UTC reset |
| Cross-exchange symbol mapper hits unknown pair | `to_native()` raises `KeyError` | Log + return `None`; caller skips |
| Universe sync misses delisting (exchange API quirk) | Manual spot-check via admin UI | Manual `tools/data/mark_delisted.py` script for one-off corrections |
| `universe_history` table grows unbounded | ~100K rows max (4 exchanges × ~25K total symbols ever); negligible | None needed |

**SP-3 failure does NOT brick the bot.** The existing Binance adapter from SP-0.5 still works. New adapters are additive — failure of any one doesn't affect the others or the live trading path.

---

## 11. Acceptance criteria

- [ ] All 4 adapters expose `fetch_klines()` and `list_symbols()` returning typed results
- [ ] `RateLimitedClient` correctly throttles when bucket empties (verified via tight-loop test)
- [ ] `universe_history` table populated for Binance + Bybit (Yahoo + TwelveData seeded manually with 10+ symbols each)
- [ ] `is_tradable("BTC/USDT", "2025-01-01")` returns True; `is_tradable("LUNA/USDT", "2024-01-01")` returns False after seeding the delisting
- [ ] Admin REST endpoints work: `GET /admin/adapters/health` lists 4 rows; `POST /admin/adapters/binance/sync` returns 200
- [ ] Cross-exchange symbol mapper handles all 4 exchanges' native conventions (test fixture covers each)
- [ ] No regression in existing 1040+ backend tests
- [ ] All 4 adapter modules ship with ≥10 unit tests each (40 total minimum)

---

## 12. Open questions (resolved during implementation)

| # | Question | Resolved during |
|---|---|---|
| 1 | Should adapters pre-fetch on startup (warm cache) or lazy on first use? | Phase A — lazy by default; warm cache is admin-triggered |
| 2 | Universe sync runs in main backend or separate worker container? | Phase F — main backend (one async task, low overhead) |
| 3 | What exchange does the symbol mapper consider "primary" for ambiguous lookups? | Phase F — adapter precedence: Binance > Bybit > Yahoo > TwelveData (configurable via env later) |
| 4 | Should `Candle` dataclass be reused across adapters or per-adapter? | Phase A — single shared dataclass in `_base.py` |
| 5 | How to handle exchange-specific quirks (e.g., Bybit's perpetual swap symbols vs Binance's USDT-margined futures)? | Phase C — `asset_class` field on SymbolInfo + adapter-specific filter in `list_symbols()` |

---

## 13. Reference

- Meta-plan: `docs/superpowers/specs/2026-05-01-trading-radar-meta-plan-design.md` §5.2, §5.15, §3 §177
- Existing Binance adapter: `backend/app/data/adapters/binance.py`
- Existing rate-limit module: `backend/app/data/ratelimit.py`
- pybit docs: https://github.com/bybit-exchange/pybit
- yfinance docs: https://github.com/ranaroussi/yfinance
- TwelveData docs: https://twelvedata.com/docs

---

**END OF SP-3 DATA ADAPTERS + UNIVERSE DESIGN SPEC**
