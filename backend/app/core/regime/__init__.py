"""Market-regime detection package (PR-BOT-INTELLIGENCE-UPGRADE).

Exposes :func:`compute_market_regime` (pure) and :func:`get_cached_market_regime`
(async, 24h cache) for use by ``open_position_gate``.
"""
from app.core.regime.market_regime import (
    compute_market_regime,
    get_cached_market_regime,
    reset_cache_for_tests,
)

__all__ = [
    "compute_market_regime",
    "get_cached_market_regime",
    "reset_cache_for_tests",
]
