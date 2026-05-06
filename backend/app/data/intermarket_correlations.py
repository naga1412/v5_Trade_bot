"""SP-3.5 Phase E1: 30d Pearson correlations symbol vs DXY / Gold.

Pulls 30 daily closes for the symbol (via Binance) and for ^DXY + GC=F (via
Yahoo), computes pct-change series, runs np.corrcoef. Returns (None, None)
on any fetch failure or insufficient samples (<20 paired bars).

Module-level dict cache with 1h TTL — same approach as :mod:`app.news.fear_greed`
since Redis is not yet wired into the codebase.
"""
from __future__ import annotations

import logging
import time

import numpy as np


log = logging.getLogger(__name__)

_CACHE_TTL_S: float = 3600.0
_DXY_SYMBOL = "^DXY"
_GOLD_SYMBOL = "GC=F"
_MIN_PAIRED = 20

_cache: dict[str, tuple[float, tuple[float | None, float | None]]] = {}


def _pct_change(closes: list[float]) -> np.ndarray:
    arr = np.asarray(closes, dtype=float)
    if arr.size < 2:
        return np.array([])
    return np.diff(arr) / arr[:-1]


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    n = min(len(a), len(b))
    if n < _MIN_PAIRED:
        return None
    a, b = a[-n:], b[-n:]
    if np.std(a) == 0 or np.std(b) == 0:
        return None
    c = float(np.corrcoef(a, b)[0, 1])
    if np.isnan(c):
        return None
    return max(-1.0, min(1.0, c))


async def _fetch_closes(adapter, symbol: str) -> list[float]:  # type: ignore[no-untyped-def]
    candles = await adapter.fetch_klines(symbol, "1d", limit=31)
    return [float(c.close) for c in candles]


async def compute_30d_correlations(
    symbol: str,
    *,
    binance_adapter,  # type: ignore[no-untyped-def]
    yahoo_adapter,    # type: ignore[no-untyped-def]
) -> tuple[float | None, float | None]:
    """Returns (dxy_correlation_30d, gold_correlation_30d). 1h-cached."""
    now = time.time()
    cached = _cache.get(symbol)
    if cached is not None and (now - cached[0]) < _CACHE_TTL_S:
        return cached[1]
    try:
        sym_closes = await _fetch_closes(binance_adapter, symbol)
        dxy_closes = await _fetch_closes(yahoo_adapter, _DXY_SYMBOL)
        gold_closes = await _fetch_closes(yahoo_adapter, _GOLD_SYMBOL)
    except Exception as e:  # noqa: BLE001
        log.warning("compute_30d_correlations(%s) fetch failed: %s", symbol, e)
        return (None, None)

    sym_pct = _pct_change(sym_closes)
    dxy_pct = _pct_change(dxy_closes)
    gold_pct = _pct_change(gold_closes)
    dxy_corr = _pearson(sym_pct, dxy_pct)
    gold_corr = _pearson(sym_pct, gold_pct)
    _cache[symbol] = (now, (dxy_corr, gold_corr))
    return (dxy_corr, gold_corr)
