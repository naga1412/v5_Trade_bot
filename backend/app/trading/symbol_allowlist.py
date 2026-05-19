"""PR10 — symbol allowlist + stablecoin filter pure helpers.

Pure functions, no DB / no I/O. The DB-touching path lives in
`app/db/symbol_performance_snapshots.py`; the dispatcher integration
lives in `app/trading/execution/symbol_allowlist_gate.py`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol


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
