"""Process-memory cache for ``PatternStatsLookup`` (SP-2 Phase E task E4).

The L2 aggregator wants per-bar access to historical pattern accuracies
without paying a DB round-trip on every closed candle. This module owns a
single in-process dict keyed by ``(symbol, timeframe)`` populated lazily on
the first ``get_or_load`` call for each key.

Refresh policy:
    * The SP-1 nightly stats job calls ``invalidate()`` (no args) to drop the
      whole cache so the next bar reloads fresh accuracies.
    * Admin actions that change ``pattern_stats`` rows for a specific symbol
      can call ``invalidate(symbol=..., timeframe=...)`` for surgical drops.

This is intentionally module-level state (a singleton) — both the live
prediction worker and the shadow worker share a single Python process per
container, so a process-wide cache is the simplest correct shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.scoring.layer2_patterns import (
    PatternStatsLookup,
    load_pattern_stats,
)


@dataclass
class PatternStatsCache:
    """Encapsulates the lookup dict so tests can construct fresh instances.

    The module-level helpers (`get_or_load`, `invalidate`) operate on the
    singleton ``_default`` instance; tests that want isolation can build their
    own ``PatternStatsCache()`` and call ``await cache.get_or_load(...)``.
    """
    _store: dict[tuple[str, str], PatternStatsLookup] = field(default_factory=dict)

    async def get_or_load(
        self, session: AsyncSession, *, symbol: str, timeframe: str,
    ) -> PatternStatsLookup:
        key = (symbol, timeframe)
        if key not in self._store:
            self._store[key] = await load_pattern_stats(
                session, symbol=symbol, timeframe=timeframe,
            )
        return self._store[key]

    def invalidate(
        self, *, symbol: str | None = None, timeframe: str | None = None,
    ) -> None:
        """Drop matching keys.

        - ``invalidate()`` clears every entry.
        - ``invalidate(symbol=X)`` drops every timeframe for symbol ``X``.
        - ``invalidate(timeframe=Y)`` drops every symbol for timeframe ``Y``.
        - ``invalidate(symbol=X, timeframe=Y)`` drops the single matching key.
        """
        if symbol is None and timeframe is None:
            self._store.clear()
            return
        to_drop: list[tuple[str, str]] = []
        for k_sym, k_tf in self._store:
            if symbol is not None and k_sym != symbol:
                continue
            if timeframe is not None and k_tf != timeframe:
                continue
            to_drop.append((k_sym, k_tf))
        for k in to_drop:
            self._store.pop(k, None)


_default: PatternStatsCache = PatternStatsCache()


async def get_or_load(
    session: AsyncSession, *, symbol: str, timeframe: str,
) -> PatternStatsLookup:
    """Module-level shortcut to the singleton cache (workers call this)."""
    return await _default.get_or_load(session, symbol=symbol, timeframe=timeframe)


def invalidate(
    *, symbol: str | None = None, timeframe: str | None = None,
) -> None:
    """Drop matching keys from the singleton cache. See ``PatternStatsCache.invalidate``."""
    _default.invalidate(symbol=symbol, timeframe=timeframe)


__all__ = [
    "PatternStatsCache",
    "get_or_load",
    "invalidate",
]
