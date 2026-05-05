# SP-3 — Data Adapters + Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Phases C, D, and E are file-disjoint and **safe to dispatch in parallel** to three subagents in a single batch.

**Goal:** Add 3 new exchange adapters (Bybit, Yahoo Finance via `yfinance`, TwelveData) to complement the existing Binance adapter, plus a point-in-time `universe_history` table that records when each symbol was listed/delisted on each exchange. Wire a Redis-backed rate-limited client that enforces each exchange's free-tier quotas. Add a cross-exchange symbol mapper so the same logical asset (e.g., `BTC/USDT`) can be looked up on any adapter. Surface adapter health + universe state via admin REST endpoints (frontend deferred to SP-6, mirroring the SP-2 pattern).

**Architecture:** A new `app.data.adapters._base` module declares the `ExchangeAdapter` Protocol plus the shared `Candle` and `SymbolInfo` dataclasses. Three new adapter modules (`bybit.py`, `yahoo.py`, `twelvedata.py`) each conform to that Protocol. The existing `binance.py` is refactored to also conform (rename method signatures, add `list_symbols()`). The token-bucket primitive in `app/data/ratelimit.py` is **kept and extended** with a `RateLimitedClient` wrapper that supports per-exchange/per-endpoint weights and Binance header-based bucket sync. A new `app/data/universe_sync.py` performs INSERT/UPDATE/delisted-flip diffing against `universe_history`; a daily 02:00-UTC background loop in `app/main.py` lifespan invokes it (mirroring `start_universe_refresh_task` from SP-1). The existing stub `app/data/universe.py:is_tradable` is replaced by a DB-backed query against `universe_history`. Admin REST endpoints under `/api/v1/admin/adapters/*` expose health + manual sync trigger + current universe listing, all gated by `Depends(require_admin)` from SP-0.7. Migration 0010 creates `universe_history` and `adapter_health`.

**Tech stack:** Python 3.11 / FastAPI / SQLAlchemy 2 / asyncpg / TimescaleDB · `pybit==5.7.0` (Bybit) · `yfinance==0.2.40` (Yahoo) · direct `httpx` for TwelveData · `respx==0.22.0` for HTTP mocking in tests.

**Spec reference:** [`docs/superpowers/specs/2026-05-05-SP-3-data-adapters-universe-design.md`](../specs/2026-05-05-SP-3-data-adapters-universe-design.md). When this plan and the spec disagree, the spec wins.

**Cross-cutting policy compliance map (which §5 policy each phase touches):**
- Phase A — establishes the Protocol surface that locks §3.2 contract
- Phase B — §5.15 (per-exchange rate-limit accounting; Binance `X-MBX-USED-WEIGHT-1M` header sync)
- Phase C — §5.15 (Bybit dual-bucket: spot 120 req/sec, derivatives 600 req/5sec)
- Phase D — §5.15 (Yahoo self-throttle 1 req/sec)
- Phase E — §5.15 (TwelveData daily counter, resets 00:00 UTC)
- Phase F — §5.2 (point-in-time universe / no survivorship bias) + §2.6 (admin endpoints inherit `require_admin` from SP-0.7)

---

## File Structure

This is what SP-3 creates inside the new worktree. All paths are under `worktrees/sp-3/`.

```
worktrees/sp-3/
├── backend/
│   ├── alembic/versions/
│   │   └── 2026_05_05_0010_universe_history_and_adapter_health.py    # NEW
│   ├── app/
│   │   ├── data/
│   │   │   ├── adapters/
│   │   │   │   ├── __init__.py             # MODIFIED — registry + get_adapter() factory
│   │   │   │   ├── _base.py                # NEW — ExchangeAdapter Protocol, Candle, SymbolInfo
│   │   │   │   ├── binance.py              # MODIFIED — conforms to ExchangeAdapter; uses RateLimitedClient
│   │   │   │   ├── bybit.py                # NEW
│   │   │   │   ├── yahoo.py                # NEW
│   │   │   │   └── twelvedata.py           # NEW
│   │   │   ├── ratelimit.py                # MODIFIED — keep TokenBucket; add RateLimitedClient + per-exchange config
│   │   │   ├── universe.py                 # MODIFIED — refactor is_tradable() to query universe_history
│   │   │   ├── universe_sync.py            # NEW — sync_universe() + daily background loop
│   │   │   └── symbols.py                  # NEW — to_native / from_native / is_supported
│   │   ├── api/routes/
│   │   │   └── admin_adapters.py           # NEW — /api/v1/admin/adapters/*
│   │   ├── api/schemas.py                  # MODIFIED — AdapterHealthOut, UniverseEntryOut, SyncResultOut
│   │   └── main.py                         # MODIFIED — wire start_universe_sync_task + admin_adapters router
│   ├── tests/
│   │   ├── unit/
│   │   │   ├── test_adapters_base.py       # NEW — Candle / SymbolInfo / Protocol shape
│   │   │   ├── test_symbols.py             # NEW — to_native / from_native / is_supported
│   │   │   ├── test_ratelimit_client.py    # NEW — RateLimitedClient: weights, header sync, daily counter
│   │   │   ├── test_adapter_binance.py     # NEW — list_symbols + Protocol conformance
│   │   │   ├── test_adapter_bybit.py       # NEW — ≥10 tests
│   │   │   ├── test_adapter_yahoo.py       # NEW — ≥10 tests
│   │   │   ├── test_adapter_twelvedata.py  # NEW — ≥10 tests
│   │   │   ├── test_universe_sync.py       # NEW — INSERT/UPDATE/delisted_at flip
│   │   │   └── test_universe_history.py    # NEW — is_tradable() against seeded universe_history
│   │   └── integration/
│   │       ├── test_api_admin_adapters.py  # NEW — REST endpoints
│   │       └── test_universe_sync_e2e.py   # NEW — full sync flow with mocked Binance
│   └── tools/data/
│       ├── seed_yahoo_symbols.py           # NEW — manual seed for Yahoo (DXY, SPY, GLD, AAPL, etc.)
│       ├── seed_twelvedata_symbols.py      # NEW — manual seed for TwelveData
│       └── bulk_import_binance.py          # NEW (stub) — manual one-shot historical import
└── docker-compose.yml + dev override + .env.example   (inherited from main)
```

**Frontend:** No changes in SP-3. The Adapters health sub-tab is **deferred to SP-6**, matching the deferral pattern set by SP-2's pattern-enable admin page. Backend admin REST is sufficient for v1 (admin can drive it via `curl`/Postman).

**Test count target after Phase F ships:** ~1100-1130 backend tests (~1040 baseline + ~60-90 new). Frontend stays at 187.

---

## Phase A — Worktree + scaffolding

### Task A1: Create SP-3 worktree

**Files:** none (git operation only)

- [ ] **Step 1: Verify clean main**

```bash
cd a:/v5_Trade_bot
git -c safe.directory='A:/v5_Trade_bot' status
git -c safe.directory='A:/v5_Trade_bot' log --oneline -1
```
Expected: `On branch main`, `nothing to commit, working tree clean`, and the latest commit is `e9b645a` (SP-2 ship) or later.

- [ ] **Step 2: Create worktree**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree add worktrees/sp-3 -b sp-3/main
```
Expected: `Preparing worktree (new branch 'sp-3/main')`.

- [ ] **Step 3: Verify worktree list**

```bash
git -c safe.directory='A:/v5_Trade_bot' worktree list
```
Expected output includes `worktrees/sp-3   <hash> [sp-3/main]`.

- [ ] **Step 4: Bring stack up + run baseline tests**

```bash
cd worktrees/sp-3
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: ~1040 backend tests pass (the SP-2 ship baseline). If this fails, **stop** — main is not green and no SP-3 work should start.

- [ ] **Step 5: Run frontend baseline**

```bash
cd worktrees/sp-3/frontend
docker compose -f ../docker-compose.yml -f ../docker-compose.dev.yml exec -T frontend npm run test --silent -- --run
```
Expected: 187 Vitest tests pass.

- [ ] **Step 6: All subsequent tasks operate inside `worktrees/sp-3/`.**

No commit yet (worktree has no new files).

---

### Task A2: Migration 0010 — universe_history + adapter_health

**Files:**
- Create: `worktrees/sp-3/backend/alembic/versions/2026_05_05_0010_universe_history_and_adapter_health.py`

**Design notes (apply throughout the migration):**
- `universe_history.symbol` is the **canonical** form (`BTC/USDT`), not the exchange-native form. Per-exchange native form is recoverable via `app.data.symbols.to_native(exchange, symbol)`.
- `UNIQUE (exchange, symbol)` enforces one logical row per (exchange, asset). Historical relistings are represented by mutating `delisted_at` back to NULL on the existing row plus a fresh `last_synced_at` — we do not insert a second row, because per spec §10 the universe table is "source-of-truth from external APIs" and is **not** hash-chained.
- `adapter_health` is append-only stats. Index on `(exchange, checked_at DESC)` so the admin dashboard can fetch the latest row per exchange in O(1).
- `metadata JSONB` stores adapter-specific extras (`base`, `quote`, `contract_size`, etc.) so we don't lose that info when an asset's classification changes between syncs.

- [ ] **Step 1: Write migration**

```python
"""universe_history + adapter_health (SP-3 Phase A spec §4.1, §4.3)

Revision ID: 0010_universe_history_and_adapter_health
Revises: 0009_pattern_enabled
Create Date: 2026-05-05
"""
from collections.abc import Sequence

from alembic import op


revision: str = "0010_universe_history_and_adapter_health"
down_revision: str | None = "0009_pattern_enabled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE universe_history (
            id BIGSERIAL PRIMARY KEY,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset_class TEXT NOT NULL
                CHECK (asset_class IN ('crypto', 'stock', 'fx', 'commodity', 'index')),
            listed_at TIMESTAMPTZ NOT NULL,
            delisted_at TIMESTAMPTZ,
            last_synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            metadata JSONB,
            UNIQUE (exchange, symbol)
        );
        """
    )
    op.execute(
        "CREATE INDEX universe_history_exchange_active_idx "
        "ON universe_history (exchange) WHERE delisted_at IS NULL;"
    )
    op.execute(
        "CREATE INDEX universe_history_symbol_idx "
        "ON universe_history (symbol);"
    )

    op.execute(
        """
        CREATE TABLE adapter_health (
            id BIGSERIAL PRIMARY KEY,
            exchange TEXT NOT NULL,
            checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            is_healthy BOOLEAN NOT NULL,
            latency_ms INTEGER,
            error_message TEXT,
            quota_used_pct DOUBLE PRECISION
        );
        """
    )
    op.execute(
        "CREATE INDEX adapter_health_recent_idx "
        "ON adapter_health (exchange, checked_at DESC);"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS adapter_health_recent_idx;")
    op.execute("DROP TABLE IF EXISTS adapter_health;")
    op.execute("DROP INDEX IF EXISTS universe_history_symbol_idx;")
    op.execute("DROP INDEX IF EXISTS universe_history_exchange_active_idx;")
    op.execute("DROP TABLE IF EXISTS universe_history;")
```

- [ ] **Step 2: Run migration**

```bash
cd worktrees/sp-3
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend bash -c "cd /app && alembic upgrade head"
```
Expected: `Running upgrade 0009_pattern_enabled -> 0010_universe_history_and_adapter_health`.

- [ ] **Step 3: Verify schema**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres \
  psql -U postgres trading_radar -c "\d universe_history" -c "\d adapter_health"
```
Expected: both tables listed with the indices declared above.

- [ ] **Step 4: Verify downgrade works (dry-run safety)**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend bash -c "cd /app && alembic downgrade -1 && alembic upgrade head"
```
Expected: clean down/up cycle leaves us back at head.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/alembic/versions/2026_05_05_0010_universe_history_and_adapter_health.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): migration 0010 — universe_history + adapter_health"
```

---

### Task A3: ExchangeAdapter Protocol + Candle/SymbolInfo dataclasses — failing test

**Files:**
- Create: `worktrees/sp-3/backend/app/data/adapters/_base.py` (stub)
- Create: `worktrees/sp-3/backend/tests/unit/test_adapters_base.py`

**Design note:** the existing `app.core.dataquality.validator.Candle` carries `symbol` + `timeframe` fields and is used by the OHLCV pipeline + DQ alerts. The SP-3 `Candle` is a different concept: a **raw bar** from an exchange, before symbol normalization happens at the caller layer. We deliberately keep them separate to avoid back-compatibility breakage on the validator, which already has wide test coverage. The adapter `Candle` is what gets translated into the validator `Candle` at the pipeline boundary.

- [ ] **Step 1: Stub** — empty `_base.py` with just imports.

```python
"""Shared types for app.data.adapters.* (SP-3 spec §3.2)."""
```

- [ ] **Step 2: Failing test**

```python
"""Smoke tests for the SP-3 ExchangeAdapter Protocol surface."""
from datetime import datetime, timezone
from typing import get_type_hints

import pytest


def test_candle_dataclass_is_frozen_and_typed() -> None:
    from app.data.adapters._base import Candle

    c = Candle(
        ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=100.0, high=110.0, low=99.0, close=105.0, volume=1234.5,
    )
    assert c.open == 100.0
    with pytest.raises(Exception):  # frozen → assignment raises
        c.open = 999.0  # type: ignore[misc]


def test_symbol_info_carries_canonical_and_native() -> None:
    from app.data.adapters._base import SymbolInfo

    s = SymbolInfo(
        canonical="BTC/USDT",
        native="BTCUSDT",
        base="BTC",
        quote="USDT",
        listed_at=datetime(2017, 8, 17, tzinfo=timezone.utc),
        delisted_at=None,
        asset_class="crypto",
    )
    assert s.canonical == "BTC/USDT"
    assert s.delisted_at is None


def test_symbol_info_asset_class_literal_accepts_all_five() -> None:
    from app.data.adapters._base import SymbolInfo

    for cls in ("crypto", "stock", "fx", "commodity", "index"):
        s = SymbolInfo(
            canonical="X", native="X", base="X", quote="",
            listed_at=None, delisted_at=None, asset_class=cls,  # type: ignore[arg-type]
        )
        assert s.asset_class == cls


def test_exchange_adapter_protocol_has_required_methods() -> None:
    from app.data.adapters._base import ExchangeAdapter

    # Verify the Protocol declares fetch_klines + list_symbols + name attribute.
    hints = get_type_hints(ExchangeAdapter)
    assert "name" in hints
    # Methods are declared on the Protocol body (not in __annotations__);
    # check via hasattr on the class itself.
    assert hasattr(ExchangeAdapter, "fetch_klines")
    assert hasattr(ExchangeAdapter, "list_symbols")


@pytest.mark.asyncio
async def test_protocol_runtime_check_with_minimal_implementation() -> None:
    """A class implementing fetch_klines + list_symbols + name should satisfy the Protocol."""
    from app.data.adapters._base import Candle, ExchangeAdapter, SymbolInfo

    class FakeAdapter:
        name = "fake"

        async def fetch_klines(
            self, *, symbol: str, timeframe: str,
            limit: int = 500,
            start: datetime | None = None, end: datetime | None = None,
        ) -> list[Candle]:
            return []

        async def list_symbols(self) -> list[SymbolInfo]:
            return []

    adapter: ExchangeAdapter = FakeAdapter()
    assert (await adapter.fetch_klines(symbol="X", timeframe="1h")) == []
    assert (await adapter.list_symbols()) == []
```

- [ ] **Step 3: Run — fail** (`pytest tests/unit/test_adapters_base.py -v`). Expected: ImportError on `Candle`/`SymbolInfo`/`ExchangeAdapter`.

---

### Task A4: ExchangeAdapter implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/adapters/_base.py`

- [ ] **Step 1: Implement**

```python
"""Shared types for app.data.adapters.* (SP-3 spec §3.2).

The Protocol is intentionally narrow: only the two methods every adapter
MUST implement. Per-exchange extensions (websocket streams, contract metadata,
funding rate fetchers) live on the adapter classes themselves and are NOT
part of the Protocol — call sites that need them must accept a concrete
adapter type, not the Protocol.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable


AssetClass = Literal["crypto", "stock", "fx", "commodity", "index"]


@dataclass(frozen=True)
class Candle:
    """A single OHLCV bar straight from an exchange.

    `ts` is the bar OPEN time, in UTC (always tz-aware). The adapter is
    responsible for converting exchange-specific timestamps (ms epoch,
    seconds, ISO) into a tz-aware datetime.
    """
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class SymbolInfo:
    """Metadata for a single tradable instrument on a given exchange."""
    canonical: str            # "BTC/USDT" — internal canonical form
    native: str               # exchange-specific: "BTCUSDT" / "BTC-USD"
    base: str                 # "BTC"
    quote: str                # "USDT" — empty string for stocks/indices
    listed_at: datetime | None
    delisted_at: datetime | None
    asset_class: AssetClass


@runtime_checkable
class ExchangeAdapter(Protocol):
    """The minimum surface every data adapter must expose."""

    name: str  # "binance", "bybit", "yahoo", "twelvedata"

    async def fetch_klines(
        self, *,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]:
        """Fetch up to `limit` bars for `symbol` at `timeframe`.

        - `symbol` is the **canonical** form (`BTC/USDT`); the adapter
          converts via its own `to_native()`.
        - `start` / `end` may be None — semantics are exchange-specific
          (e.g., Binance: most-recent N bars).
        - Network/timeout errors return an empty list with a log warning.
        - Malformed JSON raises (the caller sees the failure).
        """
        ...

    async def list_symbols(self) -> list[SymbolInfo]:
        """Return all symbols currently tradable on this exchange.

        - Exchanges without a list-all endpoint (Yahoo, TwelveData free)
          return an empty list — the universe must be seeded manually for
          those adapters via tools/data/seed_*_symbols.py.
        """
        ...
```

- [ ] **Step 2: Tests pass**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest tests/unit/test_adapters_base.py -v
```
Expected: `5 passed`.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/adapters/_base.py backend/tests/unit/test_adapters_base.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): ExchangeAdapter Protocol + Candle + SymbolInfo dataclasses"
```

---

### Task A5: Cross-exchange symbol normalization — failing test

**Files:**
- Create: `worktrees/sp-3/backend/app/data/symbols.py` (stub)
- Create: `worktrees/sp-3/backend/tests/unit/test_symbols.py`

**Design notes:**
- `to_native()` and `from_native()` are **functions**, not adapter methods, so callers that don't have an adapter instance (e.g., a backtest reading historical data) can still translate.
- Heuristics first; explicit per-exchange override dicts second. Override dicts handle the long tail (e.g., Bybit's perpetual swap symbols, Yahoo's futures suffixes).
- `is_supported(exchange, canonical)` returns True/False without raising — callers use it to skip pairs the adapter can't fetch instead of trying and catching `KeyError`.
- All inputs are uppercased + stripped before lookup.

- [ ] **Step 1: Stub** — empty `symbols.py` with module docstring only.

```python
"""Cross-exchange symbol mapping (SP-3 spec §3.5)."""
```

- [ ] **Step 2: Failing test**

```python
import pytest

from app.data.symbols import (
    UnknownSymbolError,
    UnsupportedExchangeError,
    from_native,
    is_supported,
    to_native,
)


# --- to_native ---


@pytest.mark.parametrize("canonical, expected", [
    ("BTC/USDT", "BTCUSDT"),
    ("ETH/USDT", "ETHUSDT"),
    ("SOL/USDC", "SOLUSDC"),
])
def test_to_native_binance_drops_slash(canonical: str, expected: str) -> None:
    assert to_native("binance", canonical) == expected


@pytest.mark.parametrize("canonical, expected", [
    ("BTC/USDT", "BTCUSDT"),
    ("ETH/USDT", "ETHUSDT"),
])
def test_to_native_bybit_drops_slash(canonical: str, expected: str) -> None:
    assert to_native("bybit", canonical) == expected


@pytest.mark.parametrize("canonical, expected", [
    ("BTC/USDT", "BTC-USD"),     # USDT mapped to USD on Yahoo
    ("ETH/USDT", "ETH-USD"),
    ("EUR/USD", "EURUSD=X"),     # FX uses =X suffix
    ("AAPL", "AAPL"),            # stock pass-through
    ("DXY", "DX-Y.NYB"),         # explicit override
])
def test_to_native_yahoo_handles_crypto_fx_stock_index(
    canonical: str, expected: str,
) -> None:
    assert to_native("yahoo", canonical) == expected


@pytest.mark.parametrize("canonical, expected", [
    ("EUR/USD", "EUR/USD"),      # FX keeps slash on TwelveData
    ("AAPL", "AAPL"),            # stock pass-through
])
def test_to_native_twelvedata_keeps_slash_for_fx(
    canonical: str, expected: str,
) -> None:
    assert to_native("twelvedata", canonical) == expected


def test_to_native_lowercase_and_whitespace_normalized() -> None:
    assert to_native("binance", " btc/usdt ") == "BTCUSDT"


def test_to_native_unknown_exchange_raises() -> None:
    with pytest.raises(UnsupportedExchangeError):
        to_native("kraken", "BTC/USDT")


def test_to_native_unknown_symbol_with_no_heuristic_raises() -> None:
    """Yahoo: a symbol with no slash + not in stock pass-through whitelist is unknown."""
    with pytest.raises(UnknownSymbolError):
        to_native("yahoo", "DXY-NOT-IN-MAP")


# --- from_native ---


@pytest.mark.parametrize("native, expected", [
    ("BTCUSDT", "BTC/USDT"),
    ("ETHUSDT", "ETH/USDT"),
    ("SOLUSDC", "SOL/USDC"),
])
def test_from_native_binance(native: str, expected: str) -> None:
    assert from_native("binance", native) == expected


@pytest.mark.parametrize("native, expected", [
    ("BTC-USD", "BTC/USDT"),
    ("EURUSD=X", "EUR/USD"),
    ("AAPL", "AAPL"),
    ("DX-Y.NYB", "DXY"),
])
def test_from_native_yahoo(native: str, expected: str) -> None:
    assert from_native("yahoo", native) == expected


# --- is_supported ---


def test_is_supported_dxy_only_on_yahoo() -> None:
    assert is_supported("yahoo", "DXY") is True
    assert is_supported("binance", "DXY") is False
    assert is_supported("bybit", "DXY") is False


def test_is_supported_btc_usdt_on_crypto_exchanges() -> None:
    assert is_supported("binance", "BTC/USDT") is True
    assert is_supported("bybit", "BTC/USDT") is True
    assert is_supported("yahoo", "BTC/USDT") is True   # via -USD mapping
    assert is_supported("twelvedata", "BTC/USDT") is False  # no crypto mapping
```

- [ ] **Step 3: Run — fail** with ImportError on `to_native` / `from_native` / `is_supported` / error classes.

---

### Task A6: symbols.py implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/symbols.py`

- [ ] **Step 1: Implement**

```python
"""Cross-exchange symbol mapping (SP-3 spec §3.5).

Internal canonical form is "BASE/QUOTE" with slash separator, uppercase
(e.g., "BTC/USDT", "EUR/USD"). Stocks and indices are bare uppercase
identifiers without slash (e.g., "AAPL", "DXY").

`to_native(exchange, canonical) -> str` translates canonical to the
exchange's native form. `from_native(exchange, native) -> str` is the
inverse. `is_supported(exchange, canonical) -> bool` returns False without
raising for symbols an adapter can't fetch.
"""
from __future__ import annotations

from typing import Literal


Exchange = Literal["binance", "bybit", "yahoo", "twelvedata"]
_KNOWN_EXCHANGES: frozenset[str] = frozenset(
    ("binance", "bybit", "yahoo", "twelvedata"),
)


class UnsupportedExchangeError(KeyError):
    """`exchange` is not one of the supported four."""


class UnknownSymbolError(KeyError):
    """`canonical` cannot be resolved to a native form on `exchange`."""


# --- Per-exchange explicit overrides ---


# Yahoo: indices, currencies, futures use suffixes (DXY -> DX-Y.NYB,
# Gold -> GC=F, S&P 500 -> ^GSPC, etc.). The override dict catches the
# common ones; everything else falls through the heuristic.
_YAHOO_CANONICAL_TO_NATIVE: dict[str, str] = {
    "DXY": "DX-Y.NYB",
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "VIX": "^VIX",
    "GOLD": "GC=F",
    "SILVER": "SI=F",
    "OIL": "CL=F",
}
_YAHOO_NATIVE_TO_CANONICAL: dict[str, str] = {
    v: k for k, v in _YAHOO_CANONICAL_TO_NATIVE.items()
}

# Yahoo stock whitelist: symbols that pass through unchanged (no slash,
# not in the override dict). Listed explicitly so we don't accidentally
# accept arbitrary garbage as a Yahoo ticker.
_YAHOO_STOCK_WHITELIST: frozenset[str] = frozenset((
    "AAPL", "MSFT", "GOOG", "GOOGL", "AMZN", "META", "TSLA", "NVDA",
    "SPY", "QQQ", "GLD", "SLV", "USO", "TLT", "IWM", "DIA",
))


def _norm(s: str) -> str:
    return (s or "").strip().upper()


def to_native(exchange: str, canonical: str) -> str:
    """Translate a canonical symbol to its native form on `exchange`."""
    ex = exchange.lower().strip()
    if ex not in _KNOWN_EXCHANGES:
        raise UnsupportedExchangeError(exchange)
    sym = _norm(canonical)

    if ex == "binance" or ex == "bybit":
        # Crypto: drop slash, uppercase. Reject obviously-non-crypto symbols.
        if "/" not in sym:
            raise UnknownSymbolError(f"{canonical} not a crypto pair")
        return sym.replace("/", "")

    if ex == "yahoo":
        if sym in _YAHOO_CANONICAL_TO_NATIVE:
            return _YAHOO_CANONICAL_TO_NATIVE[sym]
        if "/" in sym:
            base, quote = sym.split("/", 1)
            # Crypto -> -USD; stablecoins coalesce to USD.
            if quote in ("USDT", "USDC", "BUSD", "FDUSD", "DAI"):
                return f"{base}-USD"
            # FX -> =X
            return f"{base}{quote}=X"
        if sym in _YAHOO_STOCK_WHITELIST:
            return sym
        raise UnknownSymbolError(f"{canonical} not in Yahoo override map")

    if ex == "twelvedata":
        if "/" in sym:
            base, quote = sym.split("/", 1)
            # FX keeps slash; crypto pairs are not on TwelveData free tier.
            if quote in ("USDT", "USDC", "BUSD", "FDUSD"):
                raise UnknownSymbolError(
                    f"crypto {canonical} not supported on TwelveData free tier"
                )
            return sym
        # Stocks / indices pass through.
        return sym

    raise UnsupportedExchangeError(exchange)  # pragma: no cover — defensive


def from_native(exchange: str, native: str) -> str:
    """Inverse of `to_native`."""
    ex = exchange.lower().strip()
    if ex not in _KNOWN_EXCHANGES:
        raise UnsupportedExchangeError(exchange)
    n = _norm(native)

    if ex in ("binance", "bybit"):
        for quote in ("USDT", "USDC", "BUSD", "FDUSD"):
            if n.endswith(quote):
                return f"{n[:-len(quote)]}/{quote}"
        return n

    if ex == "yahoo":
        if n in _YAHOO_NATIVE_TO_CANONICAL:
            return _YAHOO_NATIVE_TO_CANONICAL[n]
        if n.endswith("=X"):
            base_quote = n[:-2]
            # Yahoo FX is BASE+QUOTE concatenated with no separator —
            # heuristic split: 3+3.
            if len(base_quote) == 6:
                return f"{base_quote[:3]}/{base_quote[3:]}"
            return base_quote
        if "-" in n:
            base, quote = n.split("-", 1)
            # USD on Yahoo crypto -> USDT canonical
            if quote == "USD":
                return f"{base}/USDT"
            return f"{base}/{quote}"
        return n

    if ex == "twelvedata":
        return n  # canonical == native for TD

    raise UnsupportedExchangeError(exchange)  # pragma: no cover


def is_supported(exchange: str, canonical: str) -> bool:
    """Return True if `to_native` would succeed for this pair."""
    try:
        to_native(exchange, canonical)
        return True
    except (UnknownSymbolError, UnsupportedExchangeError):
        return False
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_symbols.py -v
```
Expected: `~16 passed` (one per parametrize case + standalone).

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/symbols.py backend/tests/unit/test_symbols.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): cross-exchange symbol normalization (to_native, from_native, is_supported)"
```

---

### Task A7: pyproject.toml — add pybit + yfinance

**Files:**
- Modify: `worktrees/sp-3/backend/pyproject.toml`

- [ ] **Step 1: Add to `dependencies`** (alphabetically inserted):

```toml
    "pybit==5.7.0",
    "yfinance==0.2.40",
```

(TwelveData uses direct `httpx`, no library pin needed.)

- [ ] **Step 2: Rebuild backend image so the wheels are baked in**

```bash
cd worktrees/sp-3
docker compose -f docker-compose.yml -f docker-compose.dev.yml build --no-cache backend
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d backend
```
Expected: clean build; `pybit` + `yfinance` import successfully.

- [ ] **Step 3: Smoke import**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -c "import pybit, yfinance; print(pybit.__version__, yfinance.__version__)"
```
Expected: `5.7.0 0.2.40`.

- [ ] **Step 4: Re-run baseline pytest** to ensure adding the deps didn't break anything.

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: ~1040 + 5 (A4) + ~16 (A6) tests pass.

- [ ] **Step 5: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/pyproject.toml
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): pin pybit==5.7.0 + yfinance==0.2.40"
```

---

## Phase B — Binance adapter hardening + extract shared rate-limit middleware

### Task B1: RateLimitedClient — failing test

**Files:**
- Create: `worktrees/sp-3/backend/tests/unit/test_ratelimit_client.py`

**Design notes (apply throughout Phase B):**
- The existing `TokenBucket` in `app/data/ratelimit.py` is **kept verbatim** — it's the primitive. `RateLimitedClient` is a new class that composes one or more buckets and a wrapped `httpx.AsyncClient`.
- For SP-3, the Redis backing is **optional** — we ship with in-process buckets that mirror the Redis interface, so unit tests run without a Redis container. A future task (SP-3.5 or SP-7) will swap in a real Redis-backed bucket; the abstraction here is `BucketStore` Protocol.
- Per-exchange config lives in `RateLimitedClient.__init__`. Per-endpoint weight lookup uses `endpoint_weights: dict[endpoint_key, weight]`.
- For Binance, the response header `X-MBX-USED-WEIGHT-1M` is canonical truth — after each request, `RateLimitedClient` rewinds the bucket to `capacity - header_value` so we stay in lock-step with Binance's actual accounting.
- `RateLimitExceeded` is raised when `raise_on_exhaust=True` and acquiring would block; otherwise the client sleeps until refill.

- [ ] **Step 1: Failing test**

```python
"""Unit tests for RateLimitedClient (SP-3 Phase B)."""
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.data.ratelimit import (
    RateLimitedClient,
    RateLimitExceeded,
    TokenBucket,
)


@pytest.mark.asyncio
async def test_default_endpoint_consumes_one_token() -> None:
    bucket = TokenBucket(capacity=5, refill_per_sec=1.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/x").mock(return_value=httpx.Response(200, json={}))
        client = RateLimitedClient(
            exchange="x", http=http, buckets={"default": bucket},
        )
        await client.request("GET", "https://x.test/x", endpoint_key="default")

    assert bucket.tokens == pytest.approx(4.0, abs=0.05)


@pytest.mark.asyncio
async def test_explicit_weight_overrides_default_one() -> None:
    bucket = TokenBucket(capacity=10, refill_per_sec=10.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/x").mock(return_value=httpx.Response(200))
        client = RateLimitedClient(
            exchange="x", http=http, buckets={"default": bucket},
        )
        await client.request("GET", "https://x.test/x", weight=4)
    assert bucket.tokens == pytest.approx(6.0, abs=0.1)


@pytest.mark.asyncio
async def test_endpoint_weights_lookup() -> None:
    """`endpoint_weights={'klines': 2}` → /klines call drains 2 tokens."""
    bucket = TokenBucket(capacity=10, refill_per_sec=10.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/api/v3/klines").mock(return_value=httpx.Response(200))
        client = RateLimitedClient(
            exchange="binance",
            http=http,
            buckets={"default": bucket},
            endpoint_weights={"klines": 2},
        )
        await client.request(
            "GET", "https://x.test/api/v3/klines", endpoint_key="klines",
        )
    assert bucket.tokens == pytest.approx(8.0, abs=0.1)


@pytest.mark.asyncio
async def test_binance_header_sync_rewinds_bucket() -> None:
    """X-MBX-USED-WEIGHT-1M=900 → bucket rewinds to capacity-900=300."""
    bucket = TokenBucket(capacity=1200, refill_per_sec=20.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        router.get("/api/v3/klines").mock(
            return_value=httpx.Response(
                200,
                headers={"X-MBX-USED-WEIGHT-1M": "900"},
                json=[],
            )
        )
        client = RateLimitedClient(
            exchange="binance",
            http=http,
            buckets={"default": bucket},
            sync_header="X-MBX-USED-WEIGHT-1M",
        )
        await client.request(
            "GET", "https://api.binance.com/api/v3/klines", weight=2,
        )
    # After header sync: bucket = 1200 - 900 = 300 (header is authoritative).
    assert bucket.tokens == pytest.approx(300.0, abs=1.0)


@pytest.mark.asyncio
async def test_raise_on_exhaust_when_bucket_empty() -> None:
    bucket = TokenBucket(capacity=2, refill_per_sec=0.0001)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/x").mock(return_value=httpx.Response(200))
        client = RateLimitedClient(
            exchange="x", http=http, buckets={"default": bucket},
            raise_on_exhaust=True,
        )
        await client.request("GET", "https://x.test/x")
        await client.request("GET", "https://x.test/x")
        with pytest.raises(RateLimitExceeded):
            await client.request("GET", "https://x.test/x")


@pytest.mark.asyncio
async def test_multiple_named_buckets_routed_by_endpoint_key() -> None:
    """Bybit-style: spot + derivs are independent buckets."""
    spot = TokenBucket(capacity=5, refill_per_sec=10.0)
    derivs = TokenBucket(capacity=10, refill_per_sec=10.0)
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://x.test"
    ) as router:
        router.get("/spot").mock(return_value=httpx.Response(200))
        router.get("/derivs").mock(return_value=httpx.Response(200))
        client = RateLimitedClient(
            exchange="bybit", http=http,
            buckets={"spot": spot, "derivs": derivs},
        )
        await client.request("GET", "https://x.test/spot", endpoint_key="spot")
        await client.request("GET", "https://x.test/derivs", endpoint_key="derivs")
    assert spot.tokens == pytest.approx(4.0, abs=0.1)
    assert derivs.tokens == pytest.approx(9.0, abs=0.1)


@pytest.mark.asyncio
async def test_daily_counter_bucket_resets_at_midnight_utc() -> None:
    """TwelveData-style: 800/day, hard reset 00:00 UTC."""
    from app.data.ratelimit import DailyCounterBucket

    # Inject a fixed clock that starts at 23:30 UTC and jumps to 00:01 UTC.
    times = iter([
        datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 5, 5, 23, 30, tzinfo=timezone.utc),
        datetime(2026, 5, 6, 0, 1, tzinfo=timezone.utc),
    ])
    bucket = DailyCounterBucket(daily_limit=2, _now=lambda: next(times))
    await bucket.acquire(weight=1)
    await bucket.acquire(weight=1)
    # 3rd call would block, but the clock jumped past midnight — should refill.
    await bucket.acquire(weight=1)
    assert bucket.used_today == 1
```

- [ ] **Step 2: Run — fail** with ImportError on `RateLimitedClient`, `RateLimitExceeded`, `DailyCounterBucket`.

---

### Task B2: RateLimitedClient implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/ratelimit.py`

- [ ] **Step 1: Implement** (append below the existing `TokenBucket`).

```python
"""SP-3 extensions to the SP-0 token-bucket primitive.

The original `TokenBucket` is kept verbatim — it's the primitive. New types:

- `RateLimitedClient`: wraps `httpx.AsyncClient` with one or more buckets,
  routes each request to a bucket via `endpoint_key`, and (for Binance)
  rewinds the bucket from a response header.
- `DailyCounterBucket`: a hard-counter bucket that resets at 00:00 UTC,
  used by TwelveData (800 calls/day).
- `RateLimitExceeded`: raised when `raise_on_exhaust=True` and the bucket
  cannot accept the request.
"""
import asyncio
from datetime import datetime, timezone
from typing import Callable, Mapping, Protocol

import httpx


class RateLimitExceeded(Exception):
    """Raised by RateLimitedClient when bucket is empty and raise_on_exhaust=True."""


class _BucketLike(Protocol):
    async def acquire(self, weight: float = 1.0) -> None: ...
    @property
    def tokens(self) -> float: ...


class DailyCounterBucket:
    """Simple counter that resets at 00:00 UTC. Used for TwelveData free tier.

    Not a true token bucket — there's no per-second refill, just a hard
    daily cap. `used_today` is exposed for telemetry.
    """

    def __init__(
        self, *, daily_limit: int,
        _now: Callable[[], datetime] | None = None,
    ) -> None:
        self.daily_limit = daily_limit
        self._now = _now or (lambda: datetime.now(timezone.utc))
        self._used = 0.0
        self._date = self._now().date()
        self._lock = asyncio.Lock()

    @property
    def tokens(self) -> float:
        self._maybe_reset()
        return max(0.0, self.daily_limit - self._used)

    @property
    def used_today(self) -> int:
        self._maybe_reset()
        return int(self._used)

    def _maybe_reset(self) -> None:
        today = self._now().date()
        if today != self._date:
            self._date = today
            self._used = 0.0

    async def acquire(self, weight: float = 1.0) -> None:
        async with self._lock:
            self._maybe_reset()
            if self._used + weight <= self.daily_limit:
                self._used += weight
                return
            # Wait until 00:00 UTC. In tests, the injected clock will already
            # have advanced; in prod, we sleep the actual delta.
            now = self._now()
            tomorrow = datetime.combine(
                self._date, datetime.min.time(), tzinfo=timezone.utc,
            ).replace(day=self._date.day) + _ONE_DAY
            wait_s = max(0.0, (tomorrow - now).total_seconds())
        if wait_s > 0:
            await asyncio.sleep(wait_s)
        async with self._lock:
            self._maybe_reset()
            self._used += weight


from datetime import timedelta as _td
_ONE_DAY = _td(days=1)


class RateLimitedClient:
    """httpx.AsyncClient wrapper with per-exchange/per-endpoint rate buckets."""

    def __init__(
        self, *,
        exchange: str,
        http: httpx.AsyncClient,
        buckets: Mapping[str, _BucketLike],
        endpoint_weights: Mapping[str, int] | None = None,
        sync_header: str | None = None,
        sync_capacity: float | None = None,
        raise_on_exhaust: bool = False,
    ) -> None:
        if "default" not in buckets:
            raise ValueError("buckets must contain a 'default' bucket")
        self.exchange = exchange
        self.http = http
        self.buckets: dict[str, _BucketLike] = dict(buckets)
        self.endpoint_weights = dict(endpoint_weights or {})
        self.sync_header = sync_header
        self.sync_capacity = sync_capacity
        self.raise_on_exhaust = raise_on_exhaust

    def _resolve_weight(
        self, *, endpoint_key: str, weight: int | None,
    ) -> float:
        if weight is not None:
            return float(weight)
        return float(self.endpoint_weights.get(endpoint_key, 1))

    async def request(
        self, method: str, url: str, *,
        endpoint_key: str = "default",
        weight: int | None = None,
        **httpx_kwargs,
    ) -> httpx.Response:
        bucket = self.buckets.get(endpoint_key, self.buckets["default"])
        w = self._resolve_weight(endpoint_key=endpoint_key, weight=weight)

        if self.raise_on_exhaust and bucket.tokens < w:
            raise RateLimitExceeded(
                f"{self.exchange}/{endpoint_key}: bucket empty "
                f"({bucket.tokens:.1f} < {w})"
            )

        await bucket.acquire(weight=w)
        response = await self.http.request(method, url, **httpx_kwargs)

        # Header sync (Binance): authoritative used-weight overrides our local count.
        if self.sync_header and self.sync_capacity is not None:
            header_val = response.headers.get(self.sync_header)
            if header_val is not None:
                try:
                    used = float(header_val)
                except ValueError:
                    used = 0.0
                # Rewind by setting tokens = capacity - used.
                if hasattr(bucket, "_tokens"):
                    bucket._tokens = max(0.0, self.sync_capacity - used)  # type: ignore[attr-defined]

        return response

    async def aclose(self) -> None:
        await self.http.aclose()
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_ratelimit_client.py tests/unit/test_ratelimit.py -v
```
Expected: SP-3 new tests + the 3 existing TokenBucket tests all pass (no regression).

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/ratelimit.py backend/tests/unit/test_ratelimit_client.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): RateLimitedClient + DailyCounterBucket + per-exchange weight config"
```

---

### Task B3: Refactor BinanceClient to use RateLimitedClient + add list_symbols — failing test

**Files:**
- Modify: `worktrees/sp-3/backend/tests/integration/test_binance_adapter.py` (extend, not replace)
- Create: `worktrees/sp-3/backend/tests/unit/test_adapter_binance.py`

**Design note:** the existing `BinanceClient` uses the bare `TokenBucket` directly. We refactor it to accept a `RateLimitedClient` (composition, not inheritance), keeping the public method `fetch_klines` signature identical apart from accepting the canonical symbol form. We rename the internal field `bucket` to `rate_client`. Existing callers pass either:
- `BinanceClient(http=...)` — auto-creates a `RateLimitedClient` with default Binance config (weight 1200/min, header sync enabled, `klines` weight=2).
- `BinanceClient(rate_client=existing_client)` — for tests/wiring.

We also rename the class to `BinanceAdapter` (Protocol-conformant name) and keep `BinanceClient = BinanceAdapter` as an alias so existing imports don't break.

- [ ] **Step 1: Failing tests**

```python
"""Unit tests for BinanceAdapter (SP-3 Phase B refactor)."""
import httpx
import pytest
import respx

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.binance import BinanceAdapter


@pytest.mark.asyncio
async def test_binance_adapter_satisfies_protocol() -> None:
    async with httpx.AsyncClient() as http:
        adapter = BinanceAdapter(http=http)
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == "binance"


@pytest.mark.asyncio
async def test_fetch_klines_accepts_canonical_form() -> None:
    """SP-3: callers pass canonical 'BTC/USDT', adapter translates internally."""
    sample = [[
        1777593600000, "65000.00", "65500.00", "64800.00", "65300.00", "1234.56",
        1777597199999, "0", 0, "0", "0", "0",
    ]]
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        route = router.get("/api/v3/klines").mock(
            return_value=httpx.Response(200, json=sample)
        )
        adapter = BinanceAdapter(http=http)
        bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h", limit=1)
    assert route.called
    # The outgoing request used the native form.
    call = route.calls[0]
    assert "symbol=BTCUSDT" in str(call.request.url)
    assert len(bars) == 1
    assert bars[0].close == 65300.0


@pytest.mark.asyncio
async def test_list_symbols_parses_exchangeinfo() -> None:
    sample = {
        "symbols": [
            {
                "symbol": "BTCUSDT", "status": "TRADING",
                "baseAsset": "BTC", "quoteAsset": "USDT",
                "isSpotTradingAllowed": True,
            },
            {
                "symbol": "DELISTED1", "status": "BREAK",
                "baseAsset": "X", "quoteAsset": "USDT",
                "isSpotTradingAllowed": False,
            },
        ],
    }
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        router.get("/api/v3/exchangeInfo").mock(
            return_value=httpx.Response(200, json=sample)
        )
        adapter = BinanceAdapter(http=http)
        symbols = await adapter.list_symbols()
    canonicals = {s.canonical for s in symbols}
    assert "BTC/USDT" in canonicals
    assert "X/USDT" not in canonicals  # delisted/non-trading filtered out


@pytest.mark.asyncio
async def test_header_sync_updates_bucket_after_fetch() -> None:
    sample: list = []
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com"
    ) as router:
        router.get("/api/v3/klines").mock(
            return_value=httpx.Response(
                200,
                headers={"X-MBX-USED-WEIGHT-1M": "1100"},
                json=sample,
            )
        )
        adapter = BinanceAdapter(http=http)
        await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h", limit=1)
        # Bucket should now reflect 1200 - 1100 = 100 tokens left.
        bucket = adapter.rate_client.buckets["default"]
        assert bucket.tokens == pytest.approx(100.0, abs=2.0)
```

- [ ] **Step 2: Run — fail** with ImportError on `BinanceAdapter` (only `BinanceClient` exists today).

---

### Task B4: BinanceAdapter implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/adapters/binance.py`

- [ ] **Step 1: Implement**

```python
"""Binance REST + WS adapter (SP-0.5 → SP-3 hardened).

SP-3 refactor:
- Class renamed BinanceClient → BinanceAdapter (Protocol-conformant); the
  old name is kept as an alias for backward compatibility.
- Uses RateLimitedClient with header sync via X-MBX-USED-WEIGHT-1M.
- Accepts canonical-form symbols (BTC/USDT) and translates internally.
- Adds list_symbols() backed by /api/v3/exchangeInfo.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import websockets

from app.core.dataquality.validator import Candle as ValidatorCandle
from app.data.adapters._base import Candle, SymbolInfo
from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.data.symbols import from_native, to_native


log = logging.getLogger(__name__)

_TF_TO_BINANCE = {
    "1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d",
}
_BINANCE_QUOTE_PRIORITY = ("USDT", "USDC", "BUSD", "FDUSD")


def _to_pair(binance_symbol: str) -> str:
    """BTCUSDT -> BTC/USDT (heuristic). Kept for back-compat in WS path."""
    for quote in _BINANCE_QUOTE_PRIORITY:
        if binance_symbol.endswith(quote):
            return f"{binance_symbol[:-len(quote)]}/{quote}"
    return binance_symbol


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="binance",
        http=http,
        buckets={"default": TokenBucket(capacity=1200, refill_per_sec=20.0)},
        endpoint_weights={"klines": 2, "exchangeInfo": 10},
        sync_header="X-MBX-USED-WEIGHT-1M",
        sync_capacity=1200.0,
    )


@dataclass
class BinanceAdapter:
    """SP-3 ExchangeAdapter implementation for Binance Spot."""

    http: httpx.AsyncClient
    base_url: str = "https://api.binance.com"
    rate_client: RateLimitedClient | None = None
    name: str = field(default="binance", init=False)

    def __post_init__(self) -> None:
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_klines(
        self, *, symbol: str, timeframe: str,
        limit: int = 500,
        start: datetime | None = None, end: datetime | None = None,
    ) -> list[Candle]:
        assert self.rate_client is not None
        binance_tf = _TF_TO_BINANCE[timeframe]
        native = to_native("binance", symbol)
        params: dict[str, str | int] = {
            "symbol": native, "interval": binance_tf, "limit": limit,
        }
        if start is not None:
            params["startTime"] = int(start.timestamp() * 1000)
        if end is not None:
            params["endTime"] = int(end.timestamp() * 1000)
        url = f"{self.base_url}/api/v3/klines"
        try:
            response = await self.rate_client.request(
                "GET", url, endpoint_key="klines", params=params, timeout=10.0,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("binance fetch_klines network error: %s", e)
            return []

        result: list[Candle] = []
        for row in response.json():
            result.append(
                Candle(
                    ts=datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc),
                    open=float(row[1]), high=float(row[2]),
                    low=float(row[3]),  close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        return result

    async def list_symbols(self) -> list[SymbolInfo]:
        assert self.rate_client is not None
        url = f"{self.base_url}/api/v3/exchangeInfo"
        try:
            response = await self.rate_client.request(
                "GET", url, endpoint_key="exchangeInfo", timeout=15.0,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("binance list_symbols network error: %s", e)
            return []

        out: list[SymbolInfo] = []
        for sym in response.json().get("symbols", []):
            if sym.get("status") != "TRADING":
                continue
            if not sym.get("isSpotTradingAllowed", False):
                continue
            native = sym["symbol"]
            try:
                canonical = from_native("binance", native)
            except Exception:  # noqa: BLE001
                continue
            out.append(SymbolInfo(
                canonical=canonical,
                native=native,
                base=sym.get("baseAsset", ""),
                quote=sym.get("quoteAsset", ""),
                listed_at=None,  # Binance exchangeInfo doesn't expose listed_at
                delisted_at=None,
                asset_class="crypto",
            ))
        return out


# Back-compat alias — existing imports of BinanceClient still work.
BinanceClient = BinanceAdapter


# --- WebSocket stream (unchanged from SP-0.5) ---


class BinanceKlineStream:
    """Yields only CLOSED candles (k.x == True). Reconnect-with-backoff §5.8."""

    def __init__(
        self, symbol: str, timeframe: str, *,
        base_ws_url: str = "wss://stream.binance.com:9443",
        _connect: Callable[[str], AsyncIterator[str]] | None = None,
    ) -> None:
        self.symbol = symbol
        self.timeframe = timeframe
        self.base_ws_url = base_ws_url
        self._connect = _connect
        pair = symbol.lower()
        self.url = f"{base_ws_url}/ws/{pair}@kline_{timeframe}"

    async def _real_connect(self, url: str) -> AsyncIterator[str]:
        async with websockets.connect(url, ping_interval=15, ping_timeout=10) as ws:
            async for msg in ws:
                yield msg if isinstance(msg, str) else msg.decode()

    async def stream(self) -> AsyncIterator[ValidatorCandle]:
        connect = self._connect or self._real_connect
        backoff = 1.0
        while True:
            try:
                async for raw in connect(self.url):
                    backoff = 1.0
                    payload = json.loads(raw)
                    kline = payload.get("k") if isinstance(payload, dict) else None
                    if not kline or not kline.get("x"):
                        continue
                    yield ValidatorCandle(
                        symbol=_to_pair(kline["s"]),
                        timeframe=kline["i"],
                        ts=datetime.fromtimestamp(kline["t"] / 1000, tz=timezone.utc),
                        open=float(kline["o"]), high=float(kline["h"]),
                        low=float(kline["l"]),  close=float(kline["c"]),
                        volume=float(kline["v"]),
                    )
            except Exception:  # noqa: BLE001
                await asyncio.sleep(min(30.0, backoff))
                backoff = min(30.0, backoff * 2)
```

- [ ] **Step 2: Run new + existing Binance tests**

```bash
pytest tests/unit/test_adapter_binance.py tests/integration/test_binance_adapter.py -v
```
Expected: 4 new + 2 existing pass. The existing `BinanceClient(http=http)` invocation in `test_binance_adapter.py` continues to work via the alias.

- [ ] **Step 3: Run full suite — confirm no regressions**

```bash
pytest -q
```
Expected: ~1040 + ~25 new SP-3 tests pass.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/adapters/binance.py backend/tests/unit/test_adapter_binance.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): refactor BinanceClient → BinanceAdapter (Protocol-conformant) + list_symbols + RateLimitedClient"
```

---

## Phase C — Bybit adapter (parallel-safe with D, E)

> **Parallelism note:** Phases C, D, E are **file-disjoint** (each lives in its own adapter module + its own test file). Dispatch all three to subagents in a **single batch** — see [superpowers:dispatching-parallel-agents](../../skills/dispatching-parallel-agents.md). Each subagent owns one adapter end-to-end (test → implementation → commit).

### Task C1: Bybit adapter — failing test (≥10 tests)

**Files:**
- Create: `worktrees/sp-3/backend/app/data/adapters/bybit.py` (stub)
- Create: `worktrees/sp-3/backend/tests/unit/test_adapter_bybit.py`

**Design notes (apply throughout Phase C):**
- We use **direct `httpx`** to Bybit's REST endpoint (`https://api.bybit.com`), not the `pybit` library, even though it's pinned. The `pybit` library is sync and complicates async testing; the v5 REST endpoints are simple JSON. `pybit` is pinned only as a fallback escape hatch — if Bybit's REST shape changes, we can swap to `pybit` without re-pinning. The unit tests run entirely against `respx` mocks of the JSON endpoints.
- Bybit categorizes by `category=spot|linear|inverse`. We expose **spot** + **linear (USDT-perpetual)** in `list_symbols()`; inverse is out of scope (SP-3 doesn't trade derivatives anyway).
- Two rate-limit buckets: `spot` (120 req/sec) and `derivs` (600 req/5sec = 120/sec average). Endpoint key is set per-method.
- Symbol format on Bybit is identical to Binance for spot (`BTCUSDT`); for linear perpetuals, also `BTCUSDT` (not `BTC-PERP`). So `to_native("bybit", "BTC/USDT")` works without overrides.
- Timeframe mapping: `1m`,`5m`,`15m` are `1`,`5`,`15`; `1h`,`4h` are `60`,`240`; `1d` is `D`.

- [ ] **Step 1: Stub** — empty `bybit.py` with module docstring.

- [ ] **Step 2: Failing test** — 10 tests covering: protocol conformance, fetch_klines happy path, fetch_klines empty response, fetch_klines network timeout returns [], fetch_klines malformed JSON raises, list_symbols spot, list_symbols linear, list_symbols filters non-trading, rate-limit dual bucket, timeframe mapping coverage.

```python
"""Unit tests for BybitAdapter (SP-3 Phase C — ≥10 tests)."""
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.bybit import BybitAdapter


SAMPLE_KLINE_RESPONSE = {
    "retCode": 0, "retMsg": "OK",
    "result": {
        "category": "spot", "symbol": "BTCUSDT",
        "list": [
            ["1777593600000", "65000.0", "65500.0", "64800.0", "65300.0", "1234.5", "80000000"],
        ],
    },
}

SAMPLE_INSTRUMENTS_SPOT = {
    "retCode": 0, "result": {"category": "spot", "list": [
        {"symbol": "BTCUSDT", "baseCoin": "BTC", "quoteCoin": "USDT",
         "status": "Trading"},
        {"symbol": "DELISTED", "baseCoin": "X", "quoteCoin": "USDT",
         "status": "Closed"},
    ]},
}

SAMPLE_INSTRUMENTS_LINEAR = {
    "retCode": 0, "result": {"category": "linear", "list": [
        {"symbol": "ETHUSDT", "baseCoin": "ETH", "quoteCoin": "USDT",
         "status": "Trading", "contractType": "LinearPerpetual"},
    ]},
}


@pytest.mark.asyncio
async def test_bybit_adapter_satisfies_protocol() -> None:
    async with httpx.AsyncClient() as http:
        adapter = BybitAdapter(http=http)
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == "bybit"


@pytest.mark.asyncio
async def test_fetch_klines_happy_path() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE_RESPONSE)
        )
        adapter = BybitAdapter(http=http)
        bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h", limit=1)
    assert len(bars) == 1
    b = bars[0]
    assert b.ts == datetime(2026, 5, 1, 0, 0, tzinfo=timezone.utc)
    assert b.open == 65000.0 and b.close == 65300.0
    assert b.volume == 1234.5


@pytest.mark.asyncio
async def test_fetch_klines_empty_response_returns_empty_list() -> None:
    empty = {"retCode": 0, "result": {"category": "spot",
                                      "symbol": "BTCUSDT", "list": []}}
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=empty)
        )
        adapter = BybitAdapter(http=http)
        bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_klines_network_timeout_returns_empty() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            side_effect=httpx.TimeoutException("simulated timeout")
        )
        adapter = BybitAdapter(http=http)
        bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_klines_non_zero_retcode_raises() -> None:
    err = {"retCode": 10001, "retMsg": "params error"}
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=err)
        )
        adapter = BybitAdapter(http=http)
        with pytest.raises(Exception):
            await adapter.fetch_klines(symbol="BAD/USDT", timeframe="1h")


@pytest.mark.asyncio
async def test_list_symbols_returns_spot_and_linear_combined() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/instruments-info", params={"category": "spot"}).mock(
            return_value=httpx.Response(200, json=SAMPLE_INSTRUMENTS_SPOT)
        )
        router.get("/v5/market/instruments-info", params={"category": "linear"}).mock(
            return_value=httpx.Response(200, json=SAMPLE_INSTRUMENTS_LINEAR)
        )
        adapter = BybitAdapter(http=http)
        symbols = await adapter.list_symbols()
    canonicals = {s.canonical for s in symbols}
    assert "BTC/USDT" in canonicals
    assert "ETH/USDT" in canonicals
    # Closed instruments filtered out
    assert all(s.canonical != "X/USDT" for s in symbols)


@pytest.mark.asyncio
async def test_list_symbols_filters_non_trading_status() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/instruments-info", params={"category": "spot"}).mock(
            return_value=httpx.Response(200, json=SAMPLE_INSTRUMENTS_SPOT)
        )
        router.get("/v5/market/instruments-info", params={"category": "linear"}).mock(
            return_value=httpx.Response(200, json={"retCode": 0, "result": {"list": []}})
        )
        adapter = BybitAdapter(http=http)
        symbols = await adapter.list_symbols()
    assert {s.canonical for s in symbols} == {"BTC/USDT"}


@pytest.mark.asyncio
async def test_dual_buckets_routed_correctly() -> None:
    """Spot endpoint drains spot bucket; perp endpoint drains derivs bucket."""
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE_RESPONSE)
        )
        adapter = BybitAdapter(http=http)
        before_spot = adapter.rate_client.buckets["spot"].tokens
        before_derivs = adapter.rate_client.buckets["derivs"].tokens

        await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1h",
                                   _category="spot")
        await adapter.fetch_klines(symbol="ETH/USDT", timeframe="1h",
                                   _category="linear")

        assert adapter.rate_client.buckets["spot"].tokens < before_spot
        assert adapter.rate_client.buckets["derivs"].tokens < before_derivs


@pytest.mark.parametrize("tf, expected", [
    ("1m", "1"), ("5m", "5"), ("15m", "15"),
    ("1h", "60"), ("4h", "240"), ("1d", "D"),
])
@pytest.mark.asyncio
async def test_timeframe_mapping(tf: str, expected: str) -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        route = router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE_RESPONSE)
        )
        adapter = BybitAdapter(http=http)
        await adapter.fetch_klines(symbol="BTC/USDT", timeframe=tf, limit=1)
    assert f"interval={expected}" in str(route.calls[0].request.url)


@pytest.mark.asyncio
async def test_fetch_klines_passes_start_end_as_ms() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.bybit.com"
    ) as router:
        route = router.get("/v5/market/kline").mock(
            return_value=httpx.Response(200, json=SAMPLE_KLINE_RESPONSE)
        )
        adapter = BybitAdapter(http=http)
        await adapter.fetch_klines(
            symbol="BTC/USDT", timeframe="1h",
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
    url = str(route.calls[0].request.url)
    assert "start=" in url and "end=" in url
```

- [ ] **Step 3: Run — fail** with ImportError on `BybitAdapter`.

---

### Task C2: BybitAdapter implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/adapters/bybit.py`

- [ ] **Step 1: Implement**

```python
"""Bybit v5 REST adapter (SP-3 Phase C).

Uses direct httpx (not the pybit library — kept pinned as a fallback only)
because v5 endpoints are simple JSON and async-friendly. Dual rate-limit
buckets: `spot` (120 req/sec) and `derivs` (600 req/5sec).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.data.adapters._base import Candle, SymbolInfo
from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.data.symbols import from_native, to_native


log = logging.getLogger(__name__)


_TF_TO_BYBIT = {
    "1m": "1", "5m": "5", "15m": "15",
    "1h": "60", "4h": "240", "1d": "D",
}


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="bybit",
        http=http,
        buckets={
            "default": TokenBucket(capacity=120, refill_per_sec=120.0),
            "spot": TokenBucket(capacity=120, refill_per_sec=120.0),
            "derivs": TokenBucket(capacity=600, refill_per_sec=120.0),
        },
    )


class BybitError(Exception):
    """Bybit returned a non-zero retCode."""


@dataclass
class BybitAdapter:
    http: httpx.AsyncClient
    base_url: str = "https://api.bybit.com"
    rate_client: RateLimitedClient | None = None
    name: str = field(default="bybit", init=False)

    def __post_init__(self) -> None:
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_klines(
        self, *, symbol: str, timeframe: str,
        limit: int = 500,
        start: datetime | None = None, end: datetime | None = None,
        _category: str = "spot",
    ) -> list[Candle]:
        assert self.rate_client is not None
        bybit_tf = _TF_TO_BYBIT[timeframe]
        native = to_native("bybit", symbol)
        params: dict[str, Any] = {
            "category": _category,
            "symbol": native,
            "interval": bybit_tf,
            "limit": limit,
        }
        if start is not None:
            params["start"] = int(start.timestamp() * 1000)
        if end is not None:
            params["end"] = int(end.timestamp() * 1000)
        try:
            response = await self.rate_client.request(
                "GET",
                f"{self.base_url}/v5/market/kline",
                endpoint_key=("derivs" if _category == "linear" else "spot"),
                params=params,
                timeout=10.0,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("bybit fetch_klines network error: %s", e)
            return []

        body = response.json()
        if body.get("retCode") != 0:
            raise BybitError(f"{body.get('retCode')}: {body.get('retMsg')}")

        rows = body.get("result", {}).get("list") or []
        out: list[Candle] = []
        for row in rows:
            out.append(
                Candle(
                    ts=datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
                    open=float(row[1]), high=float(row[2]),
                    low=float(row[3]),  close=float(row[4]),
                    volume=float(row[5]),
                )
            )
        # Bybit returns newest-first; reverse so callers get oldest-first.
        out.reverse()
        return out

    async def list_symbols(self) -> list[SymbolInfo]:
        assert self.rate_client is not None
        all_symbols: list[SymbolInfo] = []
        for category in ("spot", "linear"):
            try:
                response = await self.rate_client.request(
                    "GET",
                    f"{self.base_url}/v5/market/instruments-info",
                    endpoint_key=("derivs" if category == "linear" else "spot"),
                    params={"category": category},
                    timeout=15.0,
                )
                response.raise_for_status()
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                log.warning("bybit list_symbols(%s) network error: %s",
                            category, e)
                continue
            body = response.json()
            if body.get("retCode") != 0:
                continue
            for inst in body.get("result", {}).get("list", []):
                if inst.get("status") != "Trading":
                    continue
                native = inst.get("symbol", "")
                try:
                    canonical = from_native("bybit", native)
                except Exception:  # noqa: BLE001
                    continue
                all_symbols.append(SymbolInfo(
                    canonical=canonical,
                    native=native,
                    base=inst.get("baseCoin", ""),
                    quote=inst.get("quoteCoin", ""),
                    listed_at=None,
                    delisted_at=None,
                    asset_class="crypto",
                ))
        return all_symbols
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_adapter_bybit.py -v
```
Expected: 10+ pass (10 explicit tests + 6 parametrized timeframe cases = 16 total).

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/adapters/bybit.py backend/tests/unit/test_adapter_bybit.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): BybitAdapter (v5 REST, spot+linear, dual rate buckets)"
```

---

## Phase D — Yahoo adapter (parallel-safe with C, E)

### Task D1: Yahoo adapter — failing test (≥10 tests)

**Files:**
- Create: `worktrees/sp-3/backend/app/data/adapters/yahoo.py` (stub)
- Create: `worktrees/sp-3/backend/tests/unit/test_adapter_yahoo.py`

**Design notes:**
- `yfinance` is a synchronous library backed by undocumented Yahoo endpoints. We wrap each call in `asyncio.to_thread()` so the rest of the app stays async.
- Yahoo has **no list-all endpoint** — `list_symbols()` returns an empty list, and the universe is seeded manually via `tools/data/seed_yahoo_symbols.py`.
- Self-throttle: 1 req/sec via `RateLimitedClient` with a `TokenBucket(capacity=1, refill_per_sec=1.0)`. Yahoo's unofficial limit is ~200 req/min but the throttle is unannounced and can change; 1 req/sec is conservative and matches spec §5.15.
- Symbol mapping uses `to_native("yahoo", canonical)` from Phase A6 (e.g., `BTC/USDT` → `BTC-USD`, `EUR/USD` → `EURUSD=X`, `AAPL` stays `AAPL`).
- Timeframes: yfinance uses string period/interval (`"1m"`, `"5m"`, `"60m"` for hourly, `"1d"` for daily). `4h` is **not supported** — we raise `ValueError`.
- Tests inject a fake `yfinance.download` callable so we don't hit Yahoo in CI.

- [ ] **Step 1: Stub** — empty `yahoo.py` with module docstring.

- [ ] **Step 2: Failing test** — 10 tests covering: protocol, fetch_klines happy path (crypto), fetch_klines stock (AAPL), fetch_klines FX (EURUSD=X), fetch_klines empty DataFrame returns [], fetch_klines network error returns [], list_symbols returns [], unsupported 4h timeframe raises, self-throttle drains bucket, symbol mapping integration (DXY).

```python
"""Unit tests for YahooAdapter (SP-3 Phase D — ≥10 tests)."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.yahoo import YahooAdapter


def _fake_df(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"],
        index=pd.DatetimeIndex(
            [datetime(2026, 5, r[0], tzinfo=timezone.utc) for r in rows],
            name="Datetime",
        ),
    )


@pytest.mark.asyncio
async def test_yahoo_adapter_satisfies_protocol() -> None:
    adapter = YahooAdapter()
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == "yahoo"


@pytest.mark.asyncio
async def test_fetch_klines_crypto_translates_to_btc_usd() -> None:
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 65000, 65500, 64800, 65300, 65300, 1234)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1d", limit=1)
    assert fake_dl.call_args.kwargs.get("tickers") == "BTC-USD"
    assert len(bars) == 1
    assert bars[0].close == 65300


@pytest.mark.asyncio
async def test_fetch_klines_stock_passthrough() -> None:
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 175.0, 178.0, 174.5, 177.5, 177.5, 5_000_000)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d", limit=1)
    assert fake_dl.call_args.kwargs.get("tickers") == "AAPL"
    assert bars[0].close == 177.5


@pytest.mark.asyncio
async def test_fetch_klines_fx_appends_x_suffix() -> None:
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 1.10, 1.11, 1.09, 1.105, 1.105, 0)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="EUR/USD", timeframe="1d", limit=1)
    assert fake_dl.call_args.kwargs.get("tickers") == "EURUSD=X"
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_fetch_klines_empty_dataframe_returns_empty() -> None:
    fake_dl = MagicMock(return_value=pd.DataFrame())
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bars == []


@pytest.mark.asyncio
async def test_fetch_klines_network_error_returns_empty() -> None:
    fake_dl = MagicMock(side_effect=ConnectionError("yahoo timeout"))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bars == []


@pytest.mark.asyncio
async def test_list_symbols_returns_empty_per_design() -> None:
    """Spec §3.4: Yahoo has no list-all endpoint."""
    adapter = YahooAdapter()
    symbols = await adapter.list_symbols()
    assert symbols == []


@pytest.mark.asyncio
async def test_unsupported_4h_timeframe_raises() -> None:
    fake_dl = MagicMock()
    adapter = YahooAdapter(_download=fake_dl)
    with pytest.raises(ValueError, match="4h"):
        await adapter.fetch_klines(symbol="AAPL", timeframe="4h")


@pytest.mark.asyncio
async def test_self_throttle_drains_bucket() -> None:
    fake_dl = MagicMock(return_value=pd.DataFrame())
    adapter = YahooAdapter(_download=fake_dl)
    bucket = adapter.rate_client.buckets["default"]
    before = bucket.tokens
    await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bucket.tokens < before


@pytest.mark.asyncio
async def test_dxy_index_uses_explicit_override() -> None:
    fake_dl = MagicMock(return_value=_fake_df(
        [(1, 100.0, 101.0, 99.5, 100.5, 100.5, 0)],
    ))
    adapter = YahooAdapter(_download=fake_dl)
    bars = await adapter.fetch_klines(symbol="DXY", timeframe="1d")
    assert fake_dl.call_args.kwargs.get("tickers") == "DX-Y.NYB"
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_unknown_symbol_raises_unknown_symbol_error() -> None:
    """No mapping for an arbitrary string → caller sees a clean error."""
    from app.data.symbols import UnknownSymbolError
    adapter = YahooAdapter(_download=MagicMock())
    with pytest.raises(UnknownSymbolError):
        await adapter.fetch_klines(symbol="UNKNOWN-NOT-IN-MAP", timeframe="1d")
```

- [ ] **Step 3: Run — fail** with ImportError on `YahooAdapter`.

---

### Task D2: YahooAdapter implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/adapters/yahoo.py`

- [ ] **Step 1: Implement**

```python
"""Yahoo Finance adapter via yfinance (SP-3 Phase D).

yfinance is sync — we wrap calls in asyncio.to_thread. Self-throttle is
1 req/sec via a TokenBucket (Yahoo has no published rate limit; we keep it
conservative to avoid the unofficial throttle).

list_symbols() returns []. Universe must be seeded manually via
tools/data/seed_yahoo_symbols.py.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
import pandas as pd

from app.data.adapters._base import Candle, SymbolInfo
from app.data.ratelimit import RateLimitedClient, TokenBucket
from app.data.symbols import to_native


log = logging.getLogger(__name__)


_TF_TO_YAHOO_INTERVAL = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "1h": "60m", "1d": "1d",
    # "4h" intentionally absent — yfinance does not expose 4-hour bars.
}


def _default_yfinance_download() -> Callable[..., pd.DataFrame]:
    import yfinance  # local import — heavy
    return yfinance.download


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="yahoo",
        http=http,
        buckets={"default": TokenBucket(capacity=1, refill_per_sec=1.0)},
    )


@dataclass
class YahooAdapter:
    http: httpx.AsyncClient | None = None
    rate_client: RateLimitedClient | None = None
    _download: Callable[..., pd.DataFrame] | None = None
    name: str = field(default="yahoo", init=False)

    def __post_init__(self) -> None:
        if self.http is None:
            self.http = httpx.AsyncClient()
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)
        if self._download is None:
            self._download = _default_yfinance_download()

    async def fetch_klines(
        self, *, symbol: str, timeframe: str,
        limit: int = 500,
        start: datetime | None = None, end: datetime | None = None,
    ) -> list[Candle]:
        if timeframe not in _TF_TO_YAHOO_INTERVAL:
            raise ValueError(f"yfinance does not support timeframe={timeframe}")
        assert self.rate_client is not None
        assert self._download is not None

        # Map first, so unknown symbols raise BEFORE we touch the network.
        native = to_native("yahoo", symbol)

        # Throttle then run the sync yfinance call off-loop.
        # We can't go through self.rate_client.request() because yfinance
        # isn't an httpx call — drain the bucket directly.
        await self.rate_client.buckets["default"].acquire(weight=1)

        interval = _TF_TO_YAHOO_INTERVAL[timeframe]
        period = self._period_for_limit(timeframe, limit)

        kwargs: dict[str, Any] = {
            "tickers": native,
            "interval": interval,
            "progress": False,
            "auto_adjust": False,
            "threads": False,
        }
        if start is not None:
            kwargs["start"] = start
            if end is not None:
                kwargs["end"] = end
        else:
            kwargs["period"] = period

        try:
            df = await asyncio.to_thread(self._download, **kwargs)
        except (ConnectionError, TimeoutError, OSError) as e:
            log.warning("yahoo fetch_klines network error: %s", e)
            return []

        if df is None or df.empty:
            return []

        out: list[Candle] = []
        for ts, row in df.iterrows():
            ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            if ts_dt.tzinfo is None:
                ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            out.append(Candle(
                ts=ts_dt,
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=float(row["Volume"]),
            ))
        return out

    async def list_symbols(self) -> list[SymbolInfo]:
        # Spec §3.4: Yahoo has no list-all endpoint.
        return []

    @staticmethod
    def _period_for_limit(timeframe: str, limit: int) -> str:
        """yfinance period strings: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, max."""
        if timeframe == "1d":
            if limit <= 30: return "1mo"
            if limit <= 180: return "6mo"
            if limit <= 365: return "1y"
            return "5y"
        if timeframe == "1h":
            return "30d" if limit <= 720 else "60d"
        return "5d"
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_adapter_yahoo.py -v
```
Expected: 10 passed.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/adapters/yahoo.py backend/tests/unit/test_adapter_yahoo.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): YahooAdapter (yfinance, self-throttle 1 req/sec, manual seed)"
```

---

### Task D3: Manual seed script for Yahoo symbols

**Files:**
- Create: `worktrees/sp-3/backend/tools/data/seed_yahoo_symbols.py`

- [ ] **Step 1: Write script**

```python
"""Seed `universe_history` with hand-picked Yahoo symbols (SP-3 Phase D).

Yahoo has no list-all endpoint — operators run this script once to bootstrap
the universe with the macro / equity instruments the bot is allowed to query.
Idempotent: ON CONFLICT DO NOTHING on (exchange, symbol).

Usage:
    docker compose exec backend python -m tools.data.seed_yahoo_symbols
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app.db.session import get_session_factory


SEEDS: list[dict] = [
    {"symbol": "DXY",      "asset_class": "index"},
    {"symbol": "SPX",      "asset_class": "index"},
    {"symbol": "NDX",      "asset_class": "index"},
    {"symbol": "VIX",      "asset_class": "index"},
    {"symbol": "GOLD",     "asset_class": "commodity"},
    {"symbol": "OIL",      "asset_class": "commodity"},
    {"symbol": "SPY",      "asset_class": "stock"},
    {"symbol": "QQQ",      "asset_class": "stock"},
    {"symbol": "GLD",      "asset_class": "commodity"},
    {"symbol": "AAPL",     "asset_class": "stock"},
    {"symbol": "EUR/USD",  "asset_class": "fx"},
    {"symbol": "USD/JPY",  "asset_class": "fx"},
]


async def main() -> None:
    import sqlalchemy as sa

    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        for entry in SEEDS:
            await session.execute(
                sa.text(
                    "INSERT INTO universe_history "
                    "(exchange, symbol, asset_class, listed_at, metadata) "
                    "VALUES ('yahoo', :s, :cls, :listed, :md) "
                    "ON CONFLICT (exchange, symbol) DO NOTHING"
                ),
                {
                    "s": entry["symbol"], "cls": entry["asset_class"],
                    "listed": now,
                    "md": json.dumps({"seeded": True}),
                },
            )
        await session.commit()
    print(f"seeded {len(SEEDS)} yahoo symbols")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke-test the script against the dev DB**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -m tools.data.seed_yahoo_symbols
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "SELECT symbol, asset_class FROM universe_history WHERE exchange='yahoo' ORDER BY symbol;"
```
Expected: 12 yahoo rows visible.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/tools/data/seed_yahoo_symbols.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): seed_yahoo_symbols.py — bootstrap 12 macro/equity symbols"
```

---

## Phase E — TwelveData adapter (parallel-safe with C, D)

### Task E1: TwelveData adapter — failing test (≥10 tests)

**Files:**
- Create: `worktrees/sp-3/backend/app/data/adapters/twelvedata.py` (stub)
- Create: `worktrees/sp-3/backend/tests/unit/test_adapter_twelvedata.py`

**Design notes:**
- Direct httpx (no library). Free tier base URL `https://api.twelvedata.com`. API key passed via `?apikey=` query param.
- `apikey` read from `TWELVEDATA_API_KEY` env var (via `get_settings()`); empty string in dev/CI causes the adapter to raise on first call (so tests can't accidentally hit the network).
- `DailyCounterBucket(daily_limit=800)` from Phase B for free tier accounting. The counter resets at 00:00 UTC.
- Symbol mapping uses `to_native("twelvedata", canonical)`: FX keeps slash (`EUR/USD` stays `EUR/USD`), stocks pass through (`AAPL`), crypto raises (not on free tier).
- TimeSeries endpoint: `GET /time_series?symbol=AAPL&interval=1day&outputsize=N&apikey=...`. Interval mapping: `1m`→`1min`, `5m`→`5min`, `15m`→`15min`, `1h`→`1h`, `4h`→`4h`, `1d`→`1day`.
- `list_symbols()` returns []. Per spec §3.4 the paid `/stocks` endpoint is required; free tier seeds manually via `tools/data/seed_twelvedata_symbols.py`.

- [ ] **Step 1: Stub** — empty `twelvedata.py` with module docstring.

- [ ] **Step 2: Failing test** — 10 tests covering: protocol conformance, fetch_klines stock happy path, fetch_klines FX (EUR/USD), fetch_klines empty values returns [], fetch_klines `code=429` raises rate-limit, fetch_klines network error returns [], list_symbols returns [], crypto raises UnknownSymbolError, daily counter drains, daily counter exhaustion raises, missing apikey raises.

```python
"""Unit tests for TwelveDataAdapter (SP-3 Phase E — ≥10 tests)."""
from datetime import datetime, timezone

import httpx
import pytest
import respx

from app.data.adapters._base import ExchangeAdapter
from app.data.adapters.twelvedata import (
    TwelveDataAdapter,
    TwelveDataError,
)


_TS_RESPONSE = {
    "meta": {"symbol": "AAPL", "interval": "1day", "currency": "USD",
             "exchange_timezone": "America/New_York", "exchange": "NASDAQ",
             "type": "Common Stock"},
    "values": [
        {"datetime": "2026-05-01", "open": "175.0", "high": "178.0",
         "low": "174.5", "close": "177.5", "volume": "5000000"},
    ],
    "status": "ok",
}


@pytest.mark.asyncio
async def test_twelvedata_adapter_satisfies_protocol() -> None:
    async with httpx.AsyncClient() as http:
        adapter = TwelveDataAdapter(http=http, apikey="test-key")
    assert isinstance(adapter, ExchangeAdapter)
    assert adapter.name == "twelvedata"


@pytest.mark.asyncio
async def test_fetch_klines_stock_happy_path() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com"
    ) as router:
        route = router.get("/time_series").mock(
            return_value=httpx.Response(200, json=_TS_RESPONSE)
        )
        adapter = TwelveDataAdapter(http=http, apikey="key123")
        bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d", limit=1)
    assert "symbol=AAPL" in str(route.calls[0].request.url)
    assert "apikey=key123" in str(route.calls[0].request.url)
    assert bars[0].close == 177.5


@pytest.mark.asyncio
async def test_fetch_klines_fx_keeps_slash() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com"
    ) as router:
        route = router.get("/time_series").mock(
            return_value=httpx.Response(200, json={
                **_TS_RESPONSE, "values": [
                    {"datetime": "2026-05-01", "open": "1.10", "high": "1.11",
                     "low": "1.09", "close": "1.105", "volume": "0"}
                ]})
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        await adapter.fetch_klines(symbol="EUR/USD", timeframe="1d", limit=1)
    # URL-encoded slash = %2F
    assert "symbol=EUR%2FUSD" in str(route.calls[0].request.url)


@pytest.mark.asyncio
async def test_fetch_klines_empty_values_returns_empty_list() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com"
    ) as router:
        router.get("/time_series").mock(
            return_value=httpx.Response(200, json={"values": [], "status": "ok"})
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bars == []


@pytest.mark.asyncio
async def test_429_response_raises_twelvedata_error() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com"
    ) as router:
        router.get("/time_series").mock(
            return_value=httpx.Response(200, json={
                "code": 429, "message": "rate limit exceeded", "status": "error",
            })
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        with pytest.raises(TwelveDataError, match="429"):
            await adapter.fetch_klines(symbol="AAPL", timeframe="1d")


@pytest.mark.asyncio
async def test_network_error_returns_empty_list() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com"
    ) as router:
        router.get("/time_series").mock(
            side_effect=httpx.TimeoutException("timeout"),
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        bars = await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
    assert bars == []


@pytest.mark.asyncio
async def test_list_symbols_returns_empty() -> None:
    async with httpx.AsyncClient() as http:
        adapter = TwelveDataAdapter(http=http, apikey="k")
    assert (await adapter.list_symbols()) == []


@pytest.mark.asyncio
async def test_crypto_raises_unknown_symbol_error() -> None:
    from app.data.symbols import UnknownSymbolError
    async with httpx.AsyncClient() as http:
        adapter = TwelveDataAdapter(http=http, apikey="k")
    with pytest.raises(UnknownSymbolError):
        await adapter.fetch_klines(symbol="BTC/USDT", timeframe="1d")


@pytest.mark.asyncio
async def test_daily_counter_drains() -> None:
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.twelvedata.com"
    ) as router:
        router.get("/time_series").mock(
            return_value=httpx.Response(200, json=_TS_RESPONSE)
        )
        adapter = TwelveDataAdapter(http=http, apikey="k")
        before = adapter.rate_client.buckets["default"].used_today  # type: ignore[attr-defined]
        await adapter.fetch_klines(symbol="AAPL", timeframe="1d")
        after = adapter.rate_client.buckets["default"].used_today  # type: ignore[attr-defined]
    assert after == before + 1


@pytest.mark.asyncio
async def test_missing_apikey_raises_at_construction() -> None:
    async with httpx.AsyncClient() as http:
        with pytest.raises(ValueError, match="apikey"):
            TwelveDataAdapter(http=http, apikey="")
```

- [ ] **Step 3: Run — fail** with ImportError on `TwelveDataAdapter`.

---

### Task E2: TwelveDataAdapter implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/adapters/twelvedata.py`

- [ ] **Step 1: Implement**

```python
"""TwelveData adapter — direct httpx, free tier 800 calls/day (SP-3 Phase E)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx

from app.data.adapters._base import Candle, SymbolInfo
from app.data.ratelimit import DailyCounterBucket, RateLimitedClient
from app.data.symbols import to_native


log = logging.getLogger(__name__)


_TF_TO_TWELVEDATA = {
    "1m": "1min", "5m": "5min", "15m": "15min",
    "1h": "1h", "4h": "4h", "1d": "1day",
}


class TwelveDataError(Exception):
    """TwelveData returned a non-OK status / code."""


def _default_rate_client(http: httpx.AsyncClient) -> RateLimitedClient:
    return RateLimitedClient(
        exchange="twelvedata",
        http=http,
        buckets={"default": DailyCounterBucket(daily_limit=800)},
    )


@dataclass
class TwelveDataAdapter:
    http: httpx.AsyncClient
    apikey: str
    base_url: str = "https://api.twelvedata.com"
    rate_client: RateLimitedClient | None = None
    name: str = field(default="twelvedata", init=False)

    def __post_init__(self) -> None:
        if not self.apikey:
            raise ValueError("TwelveDataAdapter requires non-empty apikey")
        if self.rate_client is None:
            self.rate_client = _default_rate_client(self.http)

    async def fetch_klines(
        self, *, symbol: str, timeframe: str,
        limit: int = 500,
        start: datetime | None = None, end: datetime | None = None,
    ) -> list[Candle]:
        assert self.rate_client is not None
        interval = _TF_TO_TWELVEDATA[timeframe]
        native = to_native("twelvedata", symbol)

        params: dict[str, Any] = {
            "symbol": native,
            "interval": interval,
            "outputsize": min(5000, max(1, limit)),
            "apikey": self.apikey,
            "format": "JSON",
        }
        if start is not None:
            params["start_date"] = start.strftime("%Y-%m-%d %H:%M:%S")
        if end is not None:
            params["end_date"] = end.strftime("%Y-%m-%d %H:%M:%S")

        try:
            response = await self.rate_client.request(
                "GET",
                f"{self.base_url}/time_series",
                params=params,
                timeout=10.0,
            )
            response.raise_for_status()
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            log.warning("twelvedata fetch_klines network error: %s", e)
            return []

        body = response.json()
        if body.get("status") == "error" or "code" in body:
            raise TwelveDataError(
                f"{body.get('code', '?')}: {body.get('message', body)}"
            )

        out: list[Candle] = []
        for v in body.get("values") or []:
            ts = datetime.fromisoformat(v["datetime"]).replace(tzinfo=timezone.utc)
            out.append(Candle(
                ts=ts,
                open=float(v["open"]),
                high=float(v["high"]),
                low=float(v["low"]),
                close=float(v["close"]),
                volume=float(v.get("volume") or 0.0),
            ))
        # TwelveData returns newest-first; reverse for oldest-first.
        out.reverse()
        return out

    async def list_symbols(self) -> list[SymbolInfo]:
        # Spec §3.4: free-tier `/stocks` is paid. Manual seed only.
        return []
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_adapter_twelvedata.py -v
```
Expected: 10 passed.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/adapters/twelvedata.py backend/tests/unit/test_adapter_twelvedata.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): TwelveDataAdapter (httpx, daily counter 800/day)"
```

---

### Task E3: Manual seed script for TwelveData symbols

**Files:**
- Create: `worktrees/sp-3/backend/tools/data/seed_twelvedata_symbols.py`

- [ ] **Step 1: Write script** (mirrors `seed_yahoo_symbols.py` with TD-appropriate symbols).

```python
"""Seed `universe_history` with hand-picked TwelveData symbols (SP-3 Phase E).

TwelveData's `/stocks` endpoint is paid; the free tier requires manual seed.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app.db.session import get_session_factory


SEEDS: list[dict] = [
    {"symbol": "AAPL",     "asset_class": "stock"},
    {"symbol": "MSFT",     "asset_class": "stock"},
    {"symbol": "GOOG",     "asset_class": "stock"},
    {"symbol": "TSLA",     "asset_class": "stock"},
    {"symbol": "EUR/USD",  "asset_class": "fx"},
    {"symbol": "USD/JPY",  "asset_class": "fx"},
    {"symbol": "GBP/USD",  "asset_class": "fx"},
    {"symbol": "USD/CHF",  "asset_class": "fx"},
    {"symbol": "AUD/USD",  "asset_class": "fx"},
    {"symbol": "NZD/USD",  "asset_class": "fx"},
]


async def main() -> None:
    import sqlalchemy as sa

    factory = get_session_factory()
    now = datetime.now(timezone.utc)
    async with factory() as session:
        for entry in SEEDS:
            await session.execute(
                sa.text(
                    "INSERT INTO universe_history "
                    "(exchange, symbol, asset_class, listed_at, metadata) "
                    "VALUES ('twelvedata', :s, :cls, :listed, :md) "
                    "ON CONFLICT (exchange, symbol) DO NOTHING"
                ),
                {
                    "s": entry["symbol"], "cls": entry["asset_class"],
                    "listed": now,
                    "md": json.dumps({"seeded": True}),
                },
            )
        await session.commit()
    print(f"seeded {len(SEEDS)} twelvedata symbols")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Smoke-test**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -m tools.data.seed_twelvedata_symbols
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T postgres psql -U postgres trading_radar -c "SELECT exchange, COUNT(*) FROM universe_history GROUP BY exchange;"
```
Expected: yahoo=12, twelvedata=10.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/tools/data/seed_twelvedata_symbols.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): seed_twelvedata_symbols.py — bootstrap 10 stock/FX symbols"
```

---

## Phase F — Universe sync + admin endpoints + ship

### Task F1: Adapter registry — failing test

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/adapters/__init__.py`
- Create: `worktrees/sp-3/backend/tests/unit/test_adapter_registry.py`

**Design note:** the registry is a module-level dict with a single `get_adapter(name) -> ExchangeAdapter` factory. Construction is **lazy** — adapters are instantiated on first `get_adapter()` call, not at import time, so test imports don't trigger Yahoo/TwelveData library loading. The shared `httpx.AsyncClient` is created on first use and shut down via an `aclose_all()` helper called from app shutdown.

- [ ] **Step 1: Failing test**

```python
import pytest

from app.data.adapters import (
    AdapterNotRegistered,
    aclose_all,
    get_adapter,
    list_registered,
)


def test_list_registered_returns_four_known_exchanges() -> None:
    names = set(list_registered())
    assert names == {"binance", "bybit", "yahoo", "twelvedata"}


@pytest.mark.asyncio
async def test_get_adapter_returns_singleton_per_name() -> None:
    a1 = get_adapter("binance")
    a2 = get_adapter("binance")
    assert a1 is a2
    await aclose_all()


@pytest.mark.asyncio
async def test_get_adapter_unknown_raises() -> None:
    with pytest.raises(AdapterNotRegistered):
        get_adapter("kraken")
```

- [ ] **Step 2: Run — fail.**

---

### Task F2: Adapter registry implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/adapters/__init__.py`

- [ ] **Step 1: Implement**

```python
"""Adapter registry + factory (SP-3 Phase F).

Lazy construction: adapters are instantiated on first `get_adapter()` call,
not at import. The shared httpx.AsyncClient is created on first use; call
`aclose_all()` from app shutdown to clean up.
"""
from __future__ import annotations

from typing import Callable

import httpx

from app.config import get_settings
from app.data.adapters._base import ExchangeAdapter


class AdapterNotRegistered(KeyError):
    """No adapter registered under `name`."""


_HTTP: httpx.AsyncClient | None = None
_INSTANCES: dict[str, ExchangeAdapter] = {}


def _shared_http() -> httpx.AsyncClient:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.AsyncClient(timeout=30.0)
    return _HTTP


def _make_binance() -> ExchangeAdapter:
    from app.data.adapters.binance import BinanceAdapter
    return BinanceAdapter(http=_shared_http())


def _make_bybit() -> ExchangeAdapter:
    from app.data.adapters.bybit import BybitAdapter
    return BybitAdapter(http=_shared_http())


def _make_yahoo() -> ExchangeAdapter:
    from app.data.adapters.yahoo import YahooAdapter
    return YahooAdapter(http=_shared_http())


def _make_twelvedata() -> ExchangeAdapter:
    from app.data.adapters.twelvedata import TwelveDataAdapter
    settings = get_settings()
    apikey = getattr(settings, "twelvedata_api_key", None) or "dev-noop-key"
    return TwelveDataAdapter(http=_shared_http(), apikey=apikey)


_FACTORIES: dict[str, Callable[[], ExchangeAdapter]] = {
    "binance": _make_binance,
    "bybit": _make_bybit,
    "yahoo": _make_yahoo,
    "twelvedata": _make_twelvedata,
}


def list_registered() -> list[str]:
    return list(_FACTORIES.keys())


def get_adapter(name: str) -> ExchangeAdapter:
    key = (name or "").lower().strip()
    if key not in _FACTORIES:
        raise AdapterNotRegistered(name)
    if key not in _INSTANCES:
        _INSTANCES[key] = _FACTORIES[key]()
    return _INSTANCES[key]


async def aclose_all() -> None:
    """Close the shared httpx client + clear cached adapter instances."""
    global _HTTP
    _INSTANCES.clear()
    if _HTTP is not None:
        await _HTTP.aclose()
        _HTTP = None
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_adapter_registry.py -v
```
Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/adapters/__init__.py backend/tests/unit/test_adapter_registry.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): adapter registry + get_adapter() factory + aclose_all()"
```

---

### Task F3: sync_universe() — failing test

**Files:**
- Create: `worktrees/sp-3/backend/app/data/universe_sync.py` (stub)
- Create: `worktrees/sp-3/backend/tests/unit/test_universe_sync.py`

**Design notes:**
- `sync_universe(adapter, session) -> SyncResult` is the unit of work. It calls `adapter.list_symbols()`, diffs the result against existing rows for that exchange in `universe_history`, and:
  - INSERTs new rows for symbols never seen before (with `listed_at = adapter.SymbolInfo.listed_at OR now`).
  - For symbols in the DB *and* in the API: leave alone, but bump `last_synced_at = now`.
  - For symbols in the DB but **not** in the API and `delisted_at IS NULL`: set `delisted_at = now` (newly delisted).
  - Already-delisted rows missing from the API: skip.
- Returns `SyncResult(added=int, still_active=int, newly_delisted=int)` — admin endpoint surfaces this.
- For Yahoo + TwelveData, `list_symbols()` returns []. `sync_universe` short-circuits with all-zero result and a log info line; the admin endpoint can still call it without erroring.
- **No SQLite ISO-string bug:** `last_synced_at`/`listed_at`/`delisted_at` are always passed as `datetime` objects (not `.isoformat()` strings). On Postgres they go straight into TIMESTAMPTZ; on SQLite (test fixtures) the driver coerces.
- Tests use the in-memory SQLite fixture from `tests/integration/conftest.py` extended with a `universe_history` table.

- [ ] **Step 1: Stub** — empty `universe_sync.py` with module docstring.

- [ ] **Step 2: Failing test**

```python
"""Unit tests for sync_universe (SP-3 Phase F)."""
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.data.adapters._base import Candle, ExchangeAdapter, SymbolInfo
from app.data.universe_sync import SyncResult, sync_universe


def _mk_engine_with_universe_history():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    return engine


async def _create_table(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE universe_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, "
            "symbol TEXT NOT NULL, "
            "asset_class TEXT NOT NULL, "
            "listed_at TIMESTAMP NOT NULL, "
            "delisted_at TIMESTAMP, "
            "last_synced_at TIMESTAMP NOT NULL, "
            "metadata TEXT, "
            "UNIQUE (exchange, symbol))"
        ))


class FakeAdapter:
    name = "binance"

    def __init__(self, symbols: list[SymbolInfo]) -> None:
        self._symbols = symbols

    async def fetch_klines(self, **kwargs) -> list[Candle]:
        return []

    async def list_symbols(self) -> list[SymbolInfo]:
        return self._symbols


def _info(canonical: str, *, asset_class: str = "crypto") -> SymbolInfo:
    return SymbolInfo(
        canonical=canonical,
        native=canonical.replace("/", ""),
        base=canonical.split("/")[0],
        quote=canonical.split("/")[-1] if "/" in canonical else "",
        listed_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        delisted_at=None,
        asset_class=asset_class,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_first_sync_inserts_all_symbols() -> None:
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    adapter = FakeAdapter([_info("BTC/USDT"), _info("ETH/USDT")])
    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session)
        await session.commit()
    assert isinstance(result, SyncResult)
    assert result.added == 2
    assert result.still_active == 0
    assert result.newly_delisted == 0


@pytest.mark.asyncio
async def test_second_sync_no_changes_yields_still_active() -> None:
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    adapter = FakeAdapter([_info("BTC/USDT")])
    async with AsyncSession(engine) as session:
        await sync_universe(adapter, session)
        await session.commit()
    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session)
        await session.commit()
    assert result.added == 0
    assert result.still_active == 1
    assert result.newly_delisted == 0


@pytest.mark.asyncio
async def test_symbol_disappears_from_api_marks_delisted() -> None:
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    full = FakeAdapter([_info("BTC/USDT"), _info("LUNA/USDT")])
    async with AsyncSession(engine) as session:
        await sync_universe(full, session)
        await session.commit()

    luna_gone = FakeAdapter([_info("BTC/USDT")])
    async with AsyncSession(engine) as session:
        result = await sync_universe(luna_gone, session)
        await session.commit()

    assert result.newly_delisted == 1

    async with AsyncSession(engine) as session:
        row = (await session.execute(
            sa.text("SELECT delisted_at FROM universe_history WHERE symbol='LUNA/USDT'")
        )).first()
    assert row is not None
    assert row.delisted_at is not None


@pytest.mark.asyncio
async def test_already_delisted_symbol_stays_delisted() -> None:
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    async with AsyncSession(engine) as session:
        await session.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, delisted_at, last_synced_at) "
            "VALUES ('binance', 'OLD/USDT', 'crypto', :l, :d, :s)"
        ), {
            "l": datetime(2018, 1, 1, tzinfo=timezone.utc),
            "d": datetime(2022, 6, 1, tzinfo=timezone.utc),
            "s": datetime(2022, 6, 1, tzinfo=timezone.utc),
        })
        await session.commit()
    adapter = FakeAdapter([])
    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session)
        await session.commit()
    assert result.newly_delisted == 0


@pytest.mark.asyncio
async def test_relisting_resets_delisted_at_to_null() -> None:
    """If a symbol comes back, sync clears delisted_at instead of inserting a duplicate."""
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    async with AsyncSession(engine) as session:
        await session.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, delisted_at, last_synced_at) "
            "VALUES ('binance', 'COME/BACK', 'crypto', :l, :d, :s)"
        ), {
            "l": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "d": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "s": datetime(2024, 1, 1, tzinfo=timezone.utc),
        })
        await session.commit()
    adapter = FakeAdapter([_info("COME/BACK")])
    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session)
        await session.commit()
    assert result.added == 0
    assert result.still_active == 1

    async with AsyncSession(engine) as session:
        row = (await session.execute(sa.text(
            "SELECT delisted_at FROM universe_history WHERE symbol='COME/BACK'"
        ))).first()
    assert row.delisted_at is None


@pytest.mark.asyncio
async def test_empty_list_symbols_short_circuits_to_zero_result() -> None:
    """Yahoo / TwelveData adapters return [] from list_symbols — sync is a no-op."""
    engine = _mk_engine_with_universe_history()
    await _create_table(engine)
    adapter = FakeAdapter([])
    async with AsyncSession(engine) as session:
        # Pre-seed with a manual entry to verify it's not flipped to delisted.
        await session.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at) "
            "VALUES ('binance', 'MANUAL/SEED', 'crypto', :l, :l)"
        ), {"l": datetime(2026, 5, 1, tzinfo=timezone.utc)})
        await session.commit()

    async with AsyncSession(engine) as session:
        result = await sync_universe(adapter, session, skip_if_empty=True)
        await session.commit()
    assert result == SyncResult(added=0, still_active=0, newly_delisted=0)

    async with AsyncSession(engine) as session:
        row = (await session.execute(sa.text(
            "SELECT delisted_at FROM universe_history WHERE symbol='MANUAL/SEED'"
        ))).first()
    assert row.delisted_at is None
```

- [ ] **Step 3: Run — fail** with ImportError on `sync_universe` / `SyncResult`.

---

### Task F4: sync_universe + daily background loop — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/universe_sync.py`

- [ ] **Step 1: Implement**

```python
"""Universe sync worker (SP-3 Phase F, spec §3.4).

Daily diff between an exchange adapter's `list_symbols()` and the
`universe_history` table. INSERT new rows, UPDATE last_synced_at on still-active
rows, set delisted_at=now on newly-missing rows.

Adapters that return [] from list_symbols (Yahoo, TwelveData) short-circuit
when `skip_if_empty=True` so the manual seeds are not flipped to delisted.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.data.adapters import get_adapter, list_registered
from app.data.adapters._base import ExchangeAdapter, SymbolInfo


log = logging.getLogger(__name__)

DEFAULT_SYNC_HOUR_UTC: int = 2  # 02:00 UTC — offset from SP-1 universe refresh (00:00 UTC)


@dataclass(frozen=True)
class SyncResult:
    added: int
    still_active: int
    newly_delisted: int


async def sync_universe(
    adapter: ExchangeAdapter,
    session: AsyncSession,
    *,
    now: datetime | None = None,
    skip_if_empty: bool = True,
) -> SyncResult:
    """Diff `adapter.list_symbols()` against universe_history; mutate accordingly.

    Caller is responsible for `session.commit()` after a successful sync.
    """
    n = now or datetime.now(UTC)
    api_symbols: list[SymbolInfo] = await adapter.list_symbols()

    if not api_symbols and skip_if_empty:
        log.info("sync_universe(%s): adapter returned 0 symbols — skipping",
                 adapter.name)
        return SyncResult(added=0, still_active=0, newly_delisted=0)

    api_set = {s.canonical for s in api_symbols}

    existing_rows = (await session.execute(
        sa.text(
            "SELECT symbol, delisted_at FROM universe_history "
            "WHERE exchange = :ex"
        ),
        {"ex": adapter.name},
    )).all()
    existing_active = {r.symbol for r in existing_rows if r.delisted_at is None}
    existing_delisted = {r.symbol for r in existing_rows if r.delisted_at is not None}

    added = 0
    still_active = 0
    newly_delisted = 0
    relisted = 0

    for sym in api_symbols:
        if sym.canonical in existing_active:
            await session.execute(
                sa.text(
                    "UPDATE universe_history "
                    "SET last_synced_at = :ts "
                    "WHERE exchange = :ex AND symbol = :s"
                ),
                {"ts": n, "ex": adapter.name, "s": sym.canonical},
            )
            still_active += 1
        elif sym.canonical in existing_delisted:
            # Relisting: clear delisted_at, refresh last_synced_at.
            await session.execute(
                sa.text(
                    "UPDATE universe_history "
                    "SET delisted_at = NULL, last_synced_at = :ts "
                    "WHERE exchange = :ex AND symbol = :s"
                ),
                {"ts": n, "ex": adapter.name, "s": sym.canonical},
            )
            still_active += 1
            relisted += 1
        else:
            await session.execute(
                sa.text(
                    "INSERT INTO universe_history "
                    "(exchange, symbol, asset_class, listed_at, last_synced_at, metadata) "
                    "VALUES (:ex, :s, :ac, :listed, :ts, :md)"
                ),
                {
                    "ex": adapter.name, "s": sym.canonical,
                    "ac": sym.asset_class,
                    "listed": sym.listed_at or n,
                    "ts": n,
                    "md": json.dumps({
                        "base": sym.base, "quote": sym.quote,
                        "native": sym.native,
                    }),
                },
            )
            added += 1

    for missing in existing_active - api_set:
        await session.execute(
            sa.text(
                "UPDATE universe_history "
                "SET delisted_at = :ts, last_synced_at = :ts "
                "WHERE exchange = :ex AND symbol = :s"
            ),
            {"ts": n, "ex": adapter.name, "s": missing},
        )
        newly_delisted += 1

    if relisted:
        log.info("sync_universe(%s): %d symbols relisted", adapter.name, relisted)
    return SyncResult(
        added=added, still_active=still_active, newly_delisted=newly_delisted,
    )


# --- Background loop (mirrors SP-1 start_universe_refresh_task pattern) ---


def _seconds_until_next_utc(hour: int, now: datetime) -> int:
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return int((target - now).total_seconds())


async def run_universe_sync_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    wake_at_utc_hour: int = DEFAULT_SYNC_HOUR_UTC,
    exchanges: list[str] | None = None,
    _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    _now: Callable[[], datetime] | None = None,
) -> None:
    now_fn = _now if _now is not None else lambda: datetime.now(UTC)
    targets = exchanges if exchanges is not None else list_registered()

    while True:
        wait_s = _seconds_until_next_utc(wake_at_utc_hour, now_fn())
        await _sleep(float(wait_s))

        for ex in targets:
            try:
                adapter = get_adapter(ex)
                async with session_factory() as session:
                    result = await sync_universe(adapter, session)
                    await session.commit()
                log.info(
                    "sync_universe(%s) done: added=%d still_active=%d "
                    "newly_delisted=%d", ex,
                    result.added, result.still_active, result.newly_delisted,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                log.error("sync_universe(%s) failed: %s", ex, e)


def start_universe_sync_task(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    wake_at_utc_hour: int = DEFAULT_SYNC_HOUR_UTC,
) -> asyncio.Task[None]:
    return asyncio.create_task(run_universe_sync_loop(
        session_factory=session_factory,
        wake_at_utc_hour=wake_at_utc_hour,
    ))


__all__ = [
    "DEFAULT_SYNC_HOUR_UTC",
    "SyncResult",
    "run_universe_sync_loop",
    "start_universe_sync_task",
    "sync_universe",
]
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_universe_sync.py -v
```
Expected: 6 passed.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/universe_sync.py backend/tests/unit/test_universe_sync.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): sync_universe + daily 02:00-UTC background loop"
```

---

### Task F5: Refactor universe.is_tradable() to query universe_history — failing test

**Files:**
- Create: `worktrees/sp-3/backend/tests/unit/test_universe_history.py`
- Modify: `worktrees/sp-3/backend/tests/unit/test_universe.py` (extend, don't drop existing tests — the BTC_USDT shortcut still needs to work for backward compat)

**Design notes:**
- `is_tradable(symbol, ts)` becomes async: it queries `universe_history`. The signature changes to `async def is_tradable(session, symbol, ts) -> bool`.
- Spec §10: returns True if **any** exchange row has `listed_at <= ts < (delisted_at OR +infinity)`.
- The `BTC_USDT` constant stays exported for callers that just need the canonical string.
- Existing tests that call `is_tradable(symbol, ts)` synchronously need to be migrated. Audit current call sites: `app/data/universe.py` is only imported in tests today (the SP-0 stub was never hooked into the live worker). So the only places to update are `tests/unit/test_universe.py`.
- Keep the existing test file's two test cases working by passing them an in-memory session pre-seeded with BTC/USDT from 2017.

- [ ] **Step 1: Failing test for the new DB-backed behavior**

```python
"""Tests for SP-3 DB-backed is_tradable() (replaces SP-0 hardcoded shortcut)."""
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.data.universe import is_tradable


async def _seed_table_and_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "CREATE TABLE universe_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, symbol TEXT NOT NULL, "
            "asset_class TEXT NOT NULL, "
            "listed_at TIMESTAMP NOT NULL, "
            "delisted_at TIMESTAMP, "
            "last_synced_at TIMESTAMP NOT NULL, "
            "metadata TEXT, "
            "UNIQUE (exchange, symbol))"
        ))
        await conn.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at) "
            "VALUES "
            "('binance', 'BTC/USDT', 'crypto', :btc_listed, :now), "
            "('binance', 'LUNA/USDT', 'crypto', :luna_listed, :now)"
        ), {
            "btc_listed": datetime(2017, 8, 17, tzinfo=timezone.utc),
            "luna_listed": datetime(2020, 8, 1, tzinfo=timezone.utc),
            "now": datetime(2026, 5, 1, tzinfo=timezone.utc),
        })
        await conn.execute(sa.text(
            "UPDATE universe_history SET delisted_at = :d "
            "WHERE symbol='LUNA/USDT'"
        ), {"d": datetime(2022, 5, 12, tzinfo=timezone.utc)})
    return engine


@pytest.mark.asyncio
async def test_btc_listed_today_is_tradable() -> None:
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "BTC/USDT", datetime(2026, 1, 1, tzinfo=timezone.utc),
        )) is True


@pytest.mark.asyncio
async def test_luna_before_listing_is_not_tradable() -> None:
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "LUNA/USDT", datetime(2018, 1, 1, tzinfo=timezone.utc),
        )) is False


@pytest.mark.asyncio
async def test_luna_during_listing_window_is_tradable() -> None:
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "LUNA/USDT", datetime(2022, 4, 1, tzinfo=timezone.utc),
        )) is True


@pytest.mark.asyncio
async def test_luna_after_delisting_is_not_tradable() -> None:
    """Spec §11 acceptance: is_tradable('LUNA/USDT', '2024-01-01') is False."""
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "LUNA/USDT", datetime(2024, 1, 1, tzinfo=timezone.utc),
        )) is False


@pytest.mark.asyncio
async def test_unknown_symbol_is_not_tradable() -> None:
    engine = await _seed_table_and_session()
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "DOES/NOTEXIST", datetime(2026, 1, 1, tzinfo=timezone.utc),
        )) is False


@pytest.mark.asyncio
async def test_any_exchange_listing_is_sufficient() -> None:
    """Spec §2 #10: tradable if ANY exchange has a matching row."""
    engine = await _seed_table_and_session()
    async with engine.begin() as conn:
        await conn.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at) "
            "VALUES ('yahoo', 'AAPL', 'stock', :l, :l)"
        ), {"l": datetime(2000, 1, 1, tzinfo=timezone.utc)})
    async with AsyncSession(engine) as session:
        assert (await is_tradable(
            session, "AAPL", datetime(2026, 1, 1, tzinfo=timezone.utc),
        )) is True
```

- [ ] **Step 2: Update existing `tests/unit/test_universe.py`** — replace the two tests with versions that use a seeded session (or move them into `test_universe_history.py` — keep the file as a thin re-export). Recommended: mark the old tests as moved and delete `test_universe.py` after porting.

- [ ] **Step 3: Run — fail.** Existing test_universe.py will fail because `is_tradable` is no longer sync.

---

### Task F6: universe.py implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/data/universe.py`

- [ ] **Step 1: Implement**

```python
"""Point-in-time universe (§5.2) — SP-3 DB-backed implementation.

Replaces the SP-0 hardcoded shortcut. `is_tradable(session, symbol, ts)`
queries `universe_history` and returns True iff ANY exchange has a row
where `listed_at <= ts AND (delisted_at IS NULL OR ts < delisted_at)`.

The function is async + takes an AsyncSession because the universe table is
a moving target (daily syncs); we query at call time, not at import.
"""
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


BTC_USDT: str = "BTC/USDT"


async def is_tradable(
    session: AsyncSession, symbol: str, ts: datetime,
) -> bool:
    """Return True if `symbol` was tradable on at least one exchange at `ts`.

    Spec §2 decision #10: "Returns `True` only if a `universe_history` row
    exists with `listed_at <= ts < (delisted_at OR +infinity)` for ANY exchange."
    """
    row = (await session.execute(
        sa.text(
            "SELECT 1 FROM universe_history "
            "WHERE symbol = :s "
            "  AND listed_at <= :ts "
            "  AND (delisted_at IS NULL OR :ts < delisted_at) "
            "LIMIT 1"
        ),
        {"s": symbol, "ts": ts},
    )).first()
    return row is not None
```

- [ ] **Step 2: Tests pass**

```bash
pytest tests/unit/test_universe_history.py -v
```
Expected: 6 passed.

- [ ] **Step 3: Verify no other modules depended on the old sync signature**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend grep -rn "from app.data.universe" /app/app /app/tests --include="*.py"
```
Expected: only the test file + `app/data/universe.py` itself reference it (the SP-0 stub was not hooked into the worker).

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/data/universe.py backend/tests/unit/test_universe_history.py backend/tests/unit/test_universe.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): is_tradable() backed by universe_history (point-in-time §5.2)"
```

---

### Task F7: Admin schemas — failing test

**Files:**
- Modify: `worktrees/sp-3/backend/app/api/schemas.py` (append new schemas)
- Create: `worktrees/sp-3/backend/tests/unit/test_schemas_adapters.py`

- [ ] **Step 1: Failing test**

```python
from datetime import datetime, timezone

from app.api.schemas import (
    AdapterHealthOut,
    SyncResultOut,
    UniverseEntryOut,
)


def test_adapter_health_out_basic() -> None:
    h = AdapterHealthOut(
        exchange="binance",
        checked_at=datetime(2026, 5, 5, tzinfo=timezone.utc),
        is_healthy=True, latency_ms=42,
        error_message=None, quota_used_pct=0.12,
    )
    assert h.is_healthy is True


def test_universe_entry_out_active_when_delisted_at_is_none() -> None:
    u = UniverseEntryOut(
        exchange="binance", symbol="BTC/USDT", asset_class="crypto",
        listed_at=datetime(2017, 8, 17, tzinfo=timezone.utc),
        delisted_at=None, last_synced_at=datetime.now(timezone.utc),
    )
    assert u.is_active is True


def test_sync_result_out() -> None:
    r = SyncResultOut(exchange="binance",
                      added=12, still_active=240, newly_delisted=3)
    assert r.added == 12
```

- [ ] **Step 2: Append to `app/api/schemas.py`** the three new models:

```python
# --- SP-3: adapter / universe schemas ---

class AdapterHealthOut(BaseModel):
    exchange: str
    checked_at: datetime
    is_healthy: bool
    latency_ms: int | None = None
    error_message: str | None = None
    quota_used_pct: float | None = Field(default=None, ge=0.0, le=1.0)


class UniverseEntryOut(BaseModel):
    exchange: str
    symbol: str
    asset_class: Literal["crypto", "stock", "fx", "commodity", "index"]
    listed_at: datetime
    delisted_at: datetime | None
    last_synced_at: datetime

    @property
    def is_active(self) -> bool:
        return self.delisted_at is None


class SyncResultOut(BaseModel):
    exchange: str
    added: int
    still_active: int
    newly_delisted: int
```

- [ ] **Step 3: Tests pass + commit.**

```bash
pytest tests/unit/test_schemas_adapters.py -v
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/api/schemas.py backend/tests/unit/test_schemas_adapters.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): Pydantic schemas for adapter health, universe entry, sync result"
```

---

### Task F8: Admin REST endpoints — failing test

**Files:**
- Create: `worktrees/sp-3/backend/app/api/routes/admin_adapters.py` (stub)
- Create: `worktrees/sp-3/backend/tests/integration/test_api_admin_adapters.py`
- Modify: `worktrees/sp-3/backend/tests/integration/conftest.py` — extend `_create_auth_tables` to also create `universe_history` + `adapter_health`

**Design note:** mirror SP-2 `admin_patterns.py` style — `APIRouter` with `prefix="/api/v1/admin/adapters"` and `dependencies=[Depends(require_admin)]` at router level. Three endpoints:
- `GET /admin/adapters/health` — list latest health row per exchange.
- `POST /admin/adapters/{exchange}/sync` — trigger universe sync immediately, return `SyncResultOut`. Uses `get_adapter(exchange)`.
- `GET /admin/universe?exchange=...&active=true&limit=100` — list `universe_history` rows with optional filters.

- [ ] **Step 1: Extend `tests/integration/conftest.py`** — add to `_create_auth_tables`:

```python
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS universe_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, symbol TEXT NOT NULL, "
            "asset_class TEXT NOT NULL, "
            "listed_at TEXT NOT NULL, "
            "delisted_at TEXT, "
            "last_synced_at TEXT NOT NULL, "
            "metadata TEXT, "
            "UNIQUE (exchange, symbol))"
        ))
        await conn.execute(sa.text(
            "CREATE TABLE IF NOT EXISTS adapter_health ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "exchange TEXT NOT NULL, "
            "checked_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "is_healthy INTEGER NOT NULL, "
            "latency_ms INTEGER, error_message TEXT, "
            "quota_used_pct REAL)"
        ))
```

- [ ] **Step 2: Stub** — empty `admin_adapters.py` with module docstring + empty router.

- [ ] **Step 3: Failing test**

```python
"""Integration tests for /api/v1/admin/adapters/* (SP-3 Phase F)."""
from __future__ import annotations

import pytest
import sqlalchemy as sa


@pytest.mark.asyncio
async def test_health_endpoint_returns_latest_per_exchange(
    admin_client, auth_factory,  # type: ignore[no-untyped-def]
) -> None:
    async with auth_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO adapter_health (exchange, checked_at, is_healthy, "
            "latency_ms, quota_used_pct) VALUES "
            "('binance', '2026-05-05 10:00:00', 1, 42, 0.12), "
            "('binance', '2026-05-05 11:00:00', 1, 38, 0.18), "
            "('bybit',   '2026-05-05 10:30:00', 0, 999, 0.99)"
        ))
        await session.commit()
    r = await admin_client.get("/api/v1/admin/adapters/health")
    assert r.status_code == 200
    body = {item["exchange"]: item for item in r.json()}
    assert body["binance"]["latency_ms"] == 38   # latest row wins
    assert body["bybit"]["is_healthy"] is False


@pytest.mark.asyncio
async def test_universe_endpoint_lists_with_filters(
    admin_client, auth_factory,  # type: ignore[no-untyped-def]
) -> None:
    async with auth_factory() as session:
        await session.execute(sa.text(
            "INSERT INTO universe_history "
            "(exchange, symbol, asset_class, listed_at, last_synced_at, delisted_at) "
            "VALUES "
            "('binance', 'BTC/USDT', 'crypto', '2017-08-17', '2026-05-01', NULL), "
            "('binance', 'LUNA/USDT', 'crypto', '2020-08-01', '2026-05-01', '2022-05-12'), "
            "('yahoo',   'AAPL',     'stock',  '2000-01-01', '2026-05-01', NULL)"
        ))
        await session.commit()
    r = await admin_client.get(
        "/api/v1/admin/universe?exchange=binance&active=true",
    )
    assert r.status_code == 200
    syms = [u["symbol"] for u in r.json()]
    assert "BTC/USDT" in syms
    assert "LUNA/USDT" not in syms
    assert "AAPL" not in syms


@pytest.mark.asyncio
async def test_sync_endpoint_triggers_sync_and_returns_counts(
    admin_client, monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """POST /admin/adapters/binance/sync invokes sync_universe + returns SyncResultOut."""
    from app.data.adapters._base import SymbolInfo
    from app.data.adapters import _FACTORIES  # type: ignore[attr-defined]

    class FakeAdapter:
        name = "binance"
        async def list_symbols(self):
            return [SymbolInfo(
                canonical="BTC/USDT", native="BTCUSDT",
                base="BTC", quote="USDT",
                listed_at=None, delisted_at=None, asset_class="crypto",
            )]
        async def fetch_klines(self, **kwargs):
            return []

    # Monkeypatch the registry to return the fake adapter.
    from app.data import adapters
    fake = FakeAdapter()
    monkeypatch.setitem(adapters._INSTANCES, "binance", fake)

    r = await admin_client.post("/api/v1/admin/adapters/binance/sync")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exchange"] == "binance"
    assert body["added"] == 1


@pytest.mark.asyncio
async def test_health_endpoint_403_for_non_admin(
    friend_client,  # type: ignore[no-untyped-def]
) -> None:
    r = await friend_client.get("/api/v1/admin/adapters/health")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sync_unknown_exchange_returns_404(admin_client) -> None:  # type: ignore[no-untyped-def]
    r = await admin_client.post("/api/v1/admin/adapters/kraken/sync")
    assert r.status_code == 404
```

- [ ] **Step 4: Run — fail.**

---

### Task F9: admin_adapters.py implementation — green

**Files:**
- Modify: `worktrees/sp-3/backend/app/api/routes/admin_adapters.py`

- [ ] **Step 1: Implement**

```python
"""Admin REST endpoints for data adapters (SP-3 Phase F).

All routes are gated by ``Depends(require_admin)`` from SP-0.7. The frontend
admin sub-page is deferred to SP-6; this dispatch ships only the backend
contract so other tooling (CLI, Postman) can drive it.

Endpoints:
- GET  /api/v1/admin/adapters/health       — latest health row per exchange
- POST /api/v1/admin/adapters/{exchange}/sync — trigger universe sync
- GET  /api/v1/admin/universe              — list universe_history rows
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AdapterHealthOut,
    SyncResultOut,
    UniverseEntryOut,
)
from app.auth.deps import require_admin
from app.data.adapters import AdapterNotRegistered, get_adapter, list_registered
from app.data.universe_sync import sync_universe
from app.db.session import get_session

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-adapters"],
    dependencies=[Depends(require_admin)],
)


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


@router.get("/adapters/health", response_model=list[AdapterHealthOut])
async def adapters_health(
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[AdapterHealthOut]:
    """Return latest health row per registered exchange.

    For exchanges with no adapter_health rows yet, returns a placeholder
    with `is_healthy=False, error_message='no checks yet'`.
    """
    rows = (await session.execute(sa.text(
        "SELECT exchange, checked_at, is_healthy, latency_ms, "
        "error_message, quota_used_pct "
        "FROM adapter_health "
        "WHERE id IN ("
        "  SELECT MAX(id) FROM adapter_health GROUP BY exchange"
        ") "
        "ORDER BY exchange ASC"
    ))).all()
    seen: dict[str, AdapterHealthOut] = {}
    for r in rows:
        seen[r.exchange] = AdapterHealthOut(
            exchange=r.exchange,
            checked_at=_coerce_dt(r.checked_at),
            is_healthy=bool(r.is_healthy),
            latency_ms=r.latency_ms,
            error_message=r.error_message,
            quota_used_pct=r.quota_used_pct,
        )
    out: list[AdapterHealthOut] = []
    for ex in list_registered():
        if ex in seen:
            out.append(seen[ex])
        else:
            out.append(AdapterHealthOut(
                exchange=ex,
                checked_at=datetime.utcnow(),
                is_healthy=False,
                error_message="no checks yet",
            ))
    return out


@router.post(
    "/adapters/{exchange}/sync", response_model=SyncResultOut,
)
async def trigger_sync(
    exchange: str,
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> SyncResultOut:
    """Manually invoke sync_universe(adapter, session) for `exchange`."""
    try:
        adapter = get_adapter(exchange)
    except AdapterNotRegistered:
        raise HTTPException(status_code=404, detail=f"unknown exchange: {exchange}")

    result = await sync_universe(adapter, session)
    await session.commit()
    return SyncResultOut(
        exchange=adapter.name,
        added=result.added,
        still_active=result.still_active,
        newly_delisted=result.newly_delisted,
    )


@router.get("/universe", response_model=list[UniverseEntryOut])
async def list_universe(
    exchange: str | None = Query(default=None),
    active: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),  # noqa: B008
) -> list[UniverseEntryOut]:
    where: list[str] = []
    params: dict[str, Any] = {"lim": limit, "off": offset}
    if exchange is not None:
        where.append("exchange = :ex")
        params["ex"] = exchange
    if active is True:
        where.append("delisted_at IS NULL")
    elif active is False:
        where.append("delisted_at IS NOT NULL")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = (await session.execute(sa.text(
        f"SELECT exchange, symbol, asset_class, listed_at, delisted_at, "
        f"last_synced_at FROM universe_history{where_sql} "
        f"ORDER BY exchange, symbol LIMIT :lim OFFSET :off"
    ), params)).all()
    return [
        UniverseEntryOut(
            exchange=r.exchange,
            symbol=r.symbol,
            asset_class=r.asset_class,
            listed_at=_coerce_dt(r.listed_at),
            delisted_at=_coerce_dt(r.delisted_at) if r.delisted_at else None,
            last_synced_at=_coerce_dt(r.last_synced_at),
        )
        for r in rows
    ]
```

- [ ] **Step 2: Wire router into `app/main.py`** — add `admin_adapters` to the import list and `app.include_router(admin_adapters.router)` block.

- [ ] **Step 3: Tests pass**

```bash
pytest tests/integration/test_api_admin_adapters.py -v
```
Expected: 5 passed.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/api/routes/admin_adapters.py backend/app/main.py backend/tests/integration/test_api_admin_adapters.py backend/tests/integration/conftest.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): admin REST endpoints (/adapters/health, /adapters/{ex}/sync, /universe)"
```

---

### Task F10: Wire daily sync loop into app lifespan + integration test

**Files:**
- Modify: `worktrees/sp-3/backend/app/main.py`
- Create: `worktrees/sp-3/backend/tests/integration/test_universe_sync_e2e.py`

**Design note:** mirror the SP-1 `start_universe_refresh_task` pattern. The sync task is started inside the `lifespan` block when `settings.env not in {"test", "ci"}` and `settings.worker_enabled` is True. On shutdown, `task.cancel()` then `await aclose_all()` for adapter cleanup.

- [ ] **Step 1: Modify `app/main.py` lifespan**

```python
# In imports:
from app.data.adapters import aclose_all as _aclose_adapters
from app.data.universe_sync import start_universe_sync_task

# In lifespan, after starting shadow_worker:
    universe_sync_task = None
    if settings.env not in {"test", "ci"} and settings.worker_enabled:
        universe_sync_task = start_universe_sync_task(get_session_factory())

# In the finally block:
        if universe_sync_task is not None:
            universe_sync_task.cancel()
        await _aclose_adapters()
```

- [ ] **Step 2: Failing E2E test**

```python
"""End-to-end test of the universe sync flow with a mocked Binance API."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest
import respx
import sqlalchemy as sa


_EXCHANGE_INFO = {
    "symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING",
         "baseAsset": "BTC", "quoteAsset": "USDT",
         "isSpotTradingAllowed": True},
        {"symbol": "ETHUSDT", "status": "TRADING",
         "baseAsset": "ETH", "quoteAsset": "USDT",
         "isSpotTradingAllowed": True},
        {"symbol": "DEAD",    "status": "BREAK",
         "baseAsset": "X",   "quoteAsset": "USDT",
         "isSpotTradingAllowed": False},
    ],
}


@pytest.mark.asyncio
async def test_full_sync_flow_inserts_then_marks_delisted(
    auth_factory,  # type: ignore[no-untyped-def]
) -> None:
    from app.data.adapters.binance import BinanceAdapter
    from app.data.universe_sync import sync_universe

    # First sync: 2 symbols inserted.
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/exchangeInfo").mock(
            return_value=httpx.Response(200, json=_EXCHANGE_INFO)
        )
        adapter = BinanceAdapter(http=http)
        async with auth_factory() as session:
            r1 = await sync_universe(adapter, session)
            await session.commit()
    assert r1.added == 2
    assert r1.still_active == 0
    assert r1.newly_delisted == 0

    # Second sync: ETH disappears -> newly_delisted=1.
    eth_gone = {"symbols": [
        {"symbol": "BTCUSDT", "status": "TRADING",
         "baseAsset": "BTC", "quoteAsset": "USDT",
         "isSpotTradingAllowed": True},
    ]}
    async with httpx.AsyncClient() as http, respx.mock(
        base_url="https://api.binance.com",
    ) as router:
        router.get("/api/v3/exchangeInfo").mock(
            return_value=httpx.Response(200, json=eth_gone)
        )
        adapter = BinanceAdapter(http=http)
        async with auth_factory() as session:
            r2 = await sync_universe(adapter, session)
            await session.commit()
    assert r2.newly_delisted == 1

    # Verify ETH delisted_at is now set.
    async with auth_factory() as session:
        row = (await session.execute(sa.text(
            "SELECT delisted_at FROM universe_history "
            "WHERE exchange='binance' AND symbol='ETH/USDT'"
        ))).first()
    assert row is not None
    assert row.delisted_at is not None
```

- [ ] **Step 3: Tests pass**

```bash
pytest tests/integration/test_universe_sync_e2e.py -v
pytest -q   # full suite — confirm no regressions
```
Expected: full suite ~1100-1130 backend tests pass.

- [ ] **Step 4: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/app/main.py backend/tests/integration/test_universe_sync_e2e.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): wire universe_sync background task into lifespan + E2E test"
```

---

### Task F11: Stub bulk_import_binance.py + ship docs

**Files:**
- Create: `worktrees/sp-3/backend/tools/data/bulk_import_binance.py`

**Design note:** spec §1 non-goals exclude scheduled bulk import; the script is a manual one-shot per the meta-plan §5.15 implementation doc. Stub it so future SP-7 has a concrete starting point.

- [ ] **Step 1: Write stub**

```python
"""Manual one-shot historical OHLCV import from Binance (SP-3 Phase F).

Pulls daily klines for the symbols currently active in universe_history
between START and END (or for the lifetime of each symbol). Writes to the
existing `ohlcv` table. Skips symbols with no universe_history row.

Cron scheduling is deferred to SP-7. Run manually:

    docker compose exec backend python -m tools.data.bulk_import_binance \\
        --start 2024-01-01 --end 2026-01-01

This script is INTENTIONALLY a single-shot CLI — see meta-plan §5.15.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from app.data.adapters import get_adapter
from app.db.session import get_session_factory


async def main(start: datetime, end: datetime) -> None:
    factory = get_session_factory()
    adapter = get_adapter("binance")

    async with factory() as session:
        rows = (await session.execute(sa.text(
            "SELECT symbol FROM universe_history "
            "WHERE exchange='binance' AND delisted_at IS NULL"
        ))).all()
    symbols = [r.symbol for r in rows]
    print(f"importing {len(symbols)} symbols from {start} to {end}")

    for sym in symbols:
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + timedelta(days=500), end)
            bars = await adapter.fetch_klines(
                symbol=sym, timeframe="1d", limit=500,
                start=cursor, end=chunk_end,
            )
            print(f"{sym}: fetched {len(bars)} bars [{cursor.date()}..{chunk_end.date()}]")
            # Insert into ohlcv table (existing schema from SP-0).
            async with factory() as session:
                for c in bars:
                    await session.execute(sa.text(
                        "INSERT INTO ohlcv (symbol, timeframe, ts, "
                        "open, high, low, close, volume) VALUES "
                        "(:s, '1d', :ts, :o, :h, :l, :c, :v) "
                        "ON CONFLICT (symbol, timeframe, ts) DO NOTHING"
                    ), {"s": sym, "ts": c.ts,
                        "o": c.open, "h": c.high, "l": c.low,
                        "c": c.close, "v": c.volume})
                await session.commit()
            cursor = chunk_end


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2024-01-01")
    parser.add_argument("--end",   type=str, default="2026-01-01")
    args = parser.parse_args()
    s = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    e = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    asyncio.run(main(s, e))
```

- [ ] **Step 2: Smoke-test only the help string** (don't actually fetch — would burn rate limits and pollute the dev DB):

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend python -m tools.data.bulk_import_binance --help
```
Expected: usage string prints.

- [ ] **Step 3: Commit**

```bash
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' add backend/tools/data/bulk_import_binance.py
git -c safe.directory='A:/v5_Trade_bot/worktrees/sp-3' -c user.name='trading-radar' -c user.email='nagarajan1998.yuva@gmail.com' commit -m "feat(sp-3): bulk_import_binance.py stub — manual one-shot historical import"
```

---

### Task F12: Final ship — full suite + tag + log entry

**Files:** none (verification + git operations)

- [ ] **Step 1: Run the full test suite — green**

```bash
cd worktrees/sp-3
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend pytest -q
```
Expected: ~1100-1130 backend tests pass. If anything red, do **not** ship — fix first.

- [ ] **Step 2: Run frontend baseline (no SP-3 changes; sanity check)**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T frontend npm run test --silent -- --run
```
Expected: 187 Vitest tests still pass.

- [ ] **Step 3: Lint + type check**

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend ruff check app/ tests/ tools/
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec -T backend mypy app/data/adapters/ app/data/symbols.py app/data/universe.py app/data/universe_sync.py app/data/ratelimit.py app/api/routes/admin_adapters.py
```
Expected: clean. Fix any ruff/mypy issues before merge.

- [ ] **Step 4: Manual acceptance check (spec §11)**

```bash
# Seed Yahoo + TwelveData manually
docker compose ... exec backend python -m tools.data.seed_yahoo_symbols
docker compose ... exec backend python -m tools.data.seed_twelvedata_symbols

# Trigger Binance sync via admin endpoint
curl -H "Cf-Access-Authenticated-User