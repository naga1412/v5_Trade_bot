"""PR10 — symbol allowlist + stablecoin filter pure helpers.

Pure functions, no DB / no I/O. The DB-touching path lives in
`app/db/symbol_performance_snapshots.py`; the dispatcher integration
lives in `app/trading/execution/symbol_allowlist_gate.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Protocol

from app.shadow.stats import (
    compute_sharpe_annualized,
    compute_win_rate,
)


# Order matters: longest first so FDUSD strips before USD, BUSD before USD, etc.
_QUOTE_SUFFIXES_LONGEST_FIRST: tuple[str, ...] = (
    "FDUSD", "USDT", "USDC", "BUSD", "TUSD", "USD",
)


def _parse_base_asset(symbol: str) -> str:
    """Extract base from BTC/USDT, BTCUSDT, BTC-USDT (case-insensitive).

    Strips known quote suffixes longest-first. Falls back to entire
    string if no recognized quote pattern.
    """
    s = symbol.replace("/", "").replace("-", "").upper()
    for quote in _QUOTE_SUFFIXES_LONGEST_FIRST:
        if s.endswith(quote) and len(s) > len(quote):
            return s[: -len(quote)]
    return s


class _StablecoinSettingsProto(Protocol):
    SHADOW_STABLECOIN_EXCLUDE_LIST: list[str]


def is_stablecoin_pair(symbol: str, settings: _StablecoinSettingsProto) -> bool:
    """True if base asset of `symbol` is in the stablecoin exclude list.

    Operator-tunable via SHADOW_STABLECOIN_EXCLUDE_LIST env override.
    """
    base = _parse_base_asset(symbol)
    excludes_upper = {b.upper() for b in settings.SHADOW_STABLECOIN_EXCLUDE_LIST}
    return base in excludes_upper


class _SnapshotProto(Protocol):
    trades_count: int
    sharpe: float | None


def is_symbol_allowed(
    snapshot: _SnapshotProto, *, grace_trades: int = 50,
) -> bool:
    """Allowlist inclusion rule.

    Allowed if either:
      - trades_count < grace_trades (new-symbol grace window), OR
      - Sharpe is strictly > 0 (positive edge demonstrated).

    Negative or zero or None Sharpe past grace → excluded. The None case
    arises when compute_sharpe_annualized returns None for < 2 trades;
    rare in practice because the grace window covers that condition.
    """
    if snapshot.trades_count < grace_trades:
        return True
    return (snapshot.sharpe or 0.0) > 0.0


@dataclass
class _AllowlistCache:
    """Process-local cache of latest snapshot per symbol.

    Single instance keyed externally on user_id. Caller holds an asyncio
    lock around rebuild to prevent thundering-herd queries.
    """
    snapshot_map: dict[str, "_SnapshotProto"] = field(default_factory=dict)
    last_refresh: datetime | None = None
    ttl: timedelta = field(default_factory=lambda: timedelta(hours=1))

    def is_fresh(self, now: datetime) -> bool:
        if self.last_refresh is None:
            return False
        return (now - self.last_refresh) < self.ttl


@dataclass(frozen=True)
class SymbolSnapshotComputed:
    """Result of computing per-symbol stats — pre-DB-insert shape."""
    symbol: str
    window_start: datetime
    window_end: datetime
    trades_count: int
    win_rate: float | None
    sharpe: float | None
    allowed: bool


class _TradeProto(Protocol):
    symbol: str
    closed_at: datetime
    pnl_usdt: float
    pnl_pct: float


class _AggregatorSettingsProto(Protocol):
    SYMBOL_ALLOWLIST_WINDOW_TRADES: int
    SYMBOL_ALLOWLIST_WINDOW_DAYS: int
    SYMBOL_ALLOWLIST_GRACE_TRADES: int


@dataclass
class _SnapForRule:
    """Internal adapter exposing the _SnapshotProto shape to is_symbol_allowed.

    Non-frozen so its attributes satisfy the settable-variable contract of
    _SnapshotProto (Protocol attributes default to read/write).
    """
    trades_count: int
    sharpe: float | None


def compute_per_symbol_stats(
    trades: Iterable[_TradeProto],
    settings: _AggregatorSettingsProto,
    *,
    now: datetime,
) -> list[SymbolSnapshotComputed]:
    """Aggregate closed trades per symbol over rolling window.

    Window: min(WINDOW_TRADES most-recent closed for the symbol,
                trades in last WINDOW_DAYS days). Smaller set wins.

    Returns one SymbolSnapshotComputed per distinct symbol present.
    Empty input → empty list.
    """
    window_start = now - timedelta(days=settings.SYMBOL_ALLOWLIST_WINDOW_DAYS)

    by_symbol: dict[str, list[_TradeProto]] = {}
    for t in trades:
        if t.closed_at < window_start:
            continue
        by_symbol.setdefault(t.symbol, []).append(t)

    out: list[SymbolSnapshotComputed] = []
    for symbol, sym_trades in by_symbol.items():
        # Sort newest first, truncate to window_trades cap.
        sym_trades.sort(key=lambda t: t.closed_at, reverse=True)
        sym_trades = sym_trades[: settings.SYMBOL_ALLOWLIST_WINDOW_TRADES]

        # Reuse existing stats helpers (shadow/stats.py). The helpers are
        # typed against the nominal Trade dataclass, but they only access
        # .pnl_pct and .pnl_usdt fields, which our _TradeProto guarantees.
        win_rate = compute_win_rate(sym_trades) if sym_trades else None  # type: ignore[arg-type]
        sharpe = (
            compute_sharpe_annualized(
                sym_trades,  # type: ignore[arg-type]
                settings.SYMBOL_ALLOWLIST_WINDOW_DAYS,
            )
            if sym_trades
            else None
        )

        allowed = is_symbol_allowed(
            _SnapForRule(trades_count=len(sym_trades), sharpe=sharpe),
            grace_trades=settings.SYMBOL_ALLOWLIST_GRACE_TRADES,
        )

        out.append(SymbolSnapshotComputed(
            symbol=symbol,
            window_start=window_start,
            window_end=now,
            trades_count=len(sym_trades),
            win_rate=win_rate,
            sharpe=sharpe,
            allowed=allowed,
        ))
    return out
