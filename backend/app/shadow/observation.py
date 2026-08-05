"""Snapshot full RL observation components at shadow-trade-open time.

When the shadow worker opens a new position, we serialize the components
needed to reconstruct the 54-float observation that the brain's PPO
policy would see at that exact moment. Stored as a JSONB blob in the
`shadow_observations` table (migration 0019), one row per position,
keyed on `signal_id` (which survives the move from
shadow_open_positions → shadow_trades on close).

This gives the offline trainer (host-tools/ml/train_brain.py via
replay_buffer.load_from_shadow_trades) EXACT obs values instead of
lossy time-based JOINs against intermarket_snapshots and regime
classifications.

Component format (stored as JSON dict, not pre-assembled float vector,
so future obs-layout changes can re-assemble from stored components):

    {
        "schema_version": 1,
        "captured_at": "2026-05-15T07:00:00.000000+00:00",
        "symbol": "ETHUSDT",
        "atr": 22.5,
        "last_close": 2245.41,
        "layer_scores": [0.1, -0.2, 0.0, 0.3, ...],   # length 9
        "market": {
            "atr_pct": 0.01,
            "funding_rate": 0.0001 | None,
            "open_interest": 1234567.0 | None,
            "oi_delta_24h": 0.0 | None,
            "regime": "neutral"
        },
        "position": {
            "cur_position": 0,
            "unrealized_pnl_R": 0.0,
            "bars_in_position": 0
        },
        "macro": {
            "weekend": false,
            "asia_open": true
        }
    }

PR-OBS-DIM-REDUCE (2026-08-05): dropped dxy_corr_30d, gold_corr_30d,
hours_to_next_high_impact, fomc_window from both `market` and `macro` —
none ever had a live collector (no FX/gold feed, no econ-calendar
source), so every row ever captured had these as hardcoded constants,
not signal. See app/rl/obs.py's module docstring for the full rationale.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.regime.market_regime import get_cached_market_regime
from app.core.features.btc_spread import compute as compute_btc_spread
from app.core.features.flow_features import compute as compute_flow_features
from app.core.features.mean_reversion import compute as compute_mean_reversion
from app.core.features.structure_location import compute as compute_structure_location
from app.core.features.volatility_state import compute as compute_volatility_state

# Maps the 3-class market_regime classifier output to the 5-class obs regime
# string. Must stay in sync with predictor.py _REGIME_MAP.
REGIME_MAPPING: dict[str | None, str] = {
    "bull": "bull_breakout",
    "bear": "bear_crash",
    "neutral": "sideways_grind",
    None: "sideways_grind",
}

log = logging.getLogger(__name__)

SCHEMA_VERSION: int = 2


def _macro_from_ts(ts: datetime) -> dict[str, Any]:
    """Derive the 2 macro features from a single timestamp (no DB needed)."""
    ts = ts.astimezone(timezone.utc) if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    weekday = ts.weekday()  # 0=Mon ... 6=Sun
    hour = ts.hour
    # asia_open: 00:00-08:00 UTC (Tokyo opens ~00:00 UTC, lunch ~03:00, closes ~07:00)
    asia_open = 0 <= hour < 8
    weekend = weekday >= 5
    return {
        "weekend": weekend,
        "asia_open": asia_open,
    }


async def _latest_intermarket_snapshot(
    session: AsyncSession, symbol: str,
) -> dict[str, Any] | None:
    """Return the most recent intermarket_snapshots row for ``symbol``, or None.

    Also computes oi_delta_24h using the same formula as
    predictor._intermarket_snapshot_for: (latest_OI - baseline_OI) / baseline_OI,
    where baseline is the latest row at-or-before captured_at - 24h.
    Returns None for oi_delta_24h when baseline is missing or OI is zero.
    """
    try:
        result = await session.execute(
            sa.text(
                "SELECT funding_rate, mark_price, open_interest, captured_at "
                "FROM intermarket_snapshots "
                "WHERE symbol = :s "
                "ORDER BY captured_at DESC LIMIT 1"
            ),
            {"s": symbol},
        )
        row = result.first()
        if row is None:
            return None

        oi_delta: float | None = None
        if row.open_interest is not None:
            # row.captured_at is a datetime in Postgres but a str in SQLite tests.
            cap_ts = row.captured_at
            if isinstance(cap_ts, str):
                cap_ts = datetime.fromisoformat(cap_ts.replace("Z", "+00:00"))
            baseline_ts = cap_ts - timedelta(hours=24)
            baseline_result = await session.execute(
                sa.text(
                    "SELECT open_interest FROM intermarket_snapshots "
                    "WHERE symbol = :s AND captured_at <= :ts "
                    "ORDER BY captured_at DESC LIMIT 1"
                ),
                {"s": symbol, "ts": baseline_ts},
            )
            baseline_row = baseline_result.first()
            if (
                baseline_row is not None
                and baseline_row.open_interest is not None
                and float(baseline_row.open_interest) > 0
            ):
                oi_delta = (
                    float(row.open_interest) - float(baseline_row.open_interest)
                ) / float(baseline_row.open_interest)

        return {
            "funding_rate": (
                float(row.funding_rate) if row.funding_rate is not None else None
            ),
            "mark_price": (
                float(row.mark_price) if row.mark_price is not None else None
            ),
            "open_interest": (
                float(row.open_interest) if row.open_interest is not None else None
            ),
            "oi_delta_24h": oi_delta,
        }
    except Exception as e:  # noqa: BLE001 — observation capture is best-effort
        log.warning("_latest_intermarket_snapshot failed for %s: %s", symbol, e)
        return None


def _build_components(
    *,
    symbol: str,
    captured_at: datetime,
    atr: float,
    last_close: float,
    layer_scores_array: list[float],
    intermarket: dict[str, Any] | None,
    regime: str = "sideways_grind",
    features: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """Pure assembler — no I/O. Easy to unit-test."""
    atr_pct = (atr / last_close) if last_close > 0 else 0.0
    funding = intermarket.get("funding_rate") if intermarket else None
    open_interest = intermarket.get("open_interest") if intermarket else None
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at.astimezone(timezone.utc).isoformat(),
        "symbol": symbol,
        "atr": atr,
        "last_close": last_close,
        "layer_scores": layer_scores_array,
        "market": {
            "atr_pct": atr_pct,
            "funding_rate": funding,
            "open_interest": open_interest,
            "oi_delta_24h": intermarket.get("oi_delta_24h") if intermarket else None,
            "regime": regime,
        },
        # At open, every position is by definition flat/just-opened.
        "position": {
            "cur_position": 0,
            "unrealized_pnl_R": 0.0,
            "bars_in_position": 0,
        },
        "macro": _macro_from_ts(captured_at),
        "features": features,
    }


async def build_obs_components(
    session: AsyncSession,
    *,
    symbol: str,
    captured_at: datetime,
    atr: float,
    last_close: float,
    layer_scores_array: list[float],
    bars: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build the per-component obs dict for one shadow-trade-open event.

    Best-effort — failures in the intermarket or regime lookups fall back to
    safe defaults (None / "sideways_grind").
    """
    intermarket = await _latest_intermarket_snapshot(session, symbol)
    raw_regime = await get_cached_market_regime()
    regime = REGIME_MAPPING[raw_regime]
    features: dict[str, float | None] | None = None
    if bars is not None:
        features = {
            **compute_mean_reversion(bars),
            **compute_volatility_state(bars),
            **compute_btc_spread(bars),
            **compute_flow_features(symbol),
            **compute_structure_location(bars),
        }
    return _build_components(
        symbol=symbol,
        captured_at=captured_at,
        atr=atr,
        last_close=last_close,
        layer_scores_array=layer_scores_array,
        intermarket=intermarket,
        regime=regime,
        features=features,
    )


async def persist_observation(
    session: AsyncSession,
    *,
    signal_id: str,
    user_id: int,
    symbol: str,
    opened_at: datetime,
    components: dict[str, Any],
) -> None:
    """Insert one row in shadow_observations.

    Best-effort — any DB error is logged and swallowed. Missing
    observation rows degrade training quality but never crash the
    shadow worker (which is critical-path).
    """
    try:
        await session.execute(
            sa.text(
                "INSERT INTO shadow_observations "
                "(signal_id, user_id, symbol, opened_at, components) "
                "VALUES (:sid, :uid, :sym, :oa, :comp)"
            ),
            {
                "sid": signal_id,
                "uid": user_id,
                "sym": symbol,
                "oa": opened_at,
                "comp": json.dumps(components),
            },
        )
    except Exception as e:  # noqa: BLE001 — observation persist is best-effort
        log.warning(
            "persist_observation failed for signal_id=%s symbol=%s: %s",
            signal_id, symbol, e,
        )


async def load_observation_components(
    session: AsyncSession, signal_id: str,
) -> dict[str, Any] | None:
    """Read the stored components dict for ``signal_id``. None if absent."""
    try:
        result = await session.execute(
            sa.text(
                "SELECT components FROM shadow_observations "
                "WHERE signal_id = :sid"
            ),
            {"sid": signal_id},
        )
        row = result.first()
        if row is None:
            return None
        raw = row.components
        # Postgres returns dict (via JSONB), SQLite returns str (TEXT).
        if isinstance(raw, str):
            return json.loads(raw)
        return raw  # type: ignore[no-any-return]
    except Exception as e:  # noqa: BLE001
        log.warning("load_observation_components failed for %s: %s", signal_id, e)
        return None


__all__ = [
    "SCHEMA_VERSION",
    "build_obs_components",
    "persist_observation",
    "load_observation_components",
]
