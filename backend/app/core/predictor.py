import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd

from app.api.schemas import (
    FinalScoreOut, LayerScoreOut, LivePredictionOut, MomentumPanelOut, TradeSetupOut,
)
from app.core.indicators.macd import macd
from app.core.indicators.rsi import rsi
from app.core.scoring.aggregator import aggregate
from app.core.scoring.layer1_macro import score as score_l1
from app.core.scoring.layer2_patterns import PatternStatsLookup, score as score_l2
from app.core.scoring.layer3_momentum import score as score_l3
from app.core.scoring.layer4_smc import score as score_l4
from app.core.scoring.layer5_volume import score as score_l5
from app.core.scoring.layer6_micro import score as score_l6
from app.core.scoring.layer7_xgboost import score as score_l7
from app.core.scoring.layer8_convlstm import GhostInput, score as score_l8
from app.core.scoring.layer10_brain import score as score_l10
from app.core.scoring.run_traps import check_all_traps
from app.core.scoring.tiers import classify_tier
from app.core.scoring.traps.base import TrapContext
from app.core.scoring.types import Direction, LayerScore

_TRAP_PENALTY: float = 0.15
_TRAP_CAP: int = 4
_SHORT_DIRECTION_PENALTY: float = 0.95


def _layer_to_out(layer: LayerScore | None) -> LayerScoreOut | None:
    if layer is None:
        return None
    return LayerScoreOut(
        direction=layer.direction.value,
        strength=layer.strength,
        confidence=layer.confidence,
        notes=layer.notes,
    )


def _compute_inputs_hash(symbol: str, timeframe: str, bars: pd.DataFrame) -> str:
    last = bars.iloc[-1]
    canon = (
        f"{symbol}|{timeframe}|{bars.index[-1].isoformat()}|"
        f"{last['open']}|{last['high']}|{last['low']}|{last['close']}|{last['volume']}"
    )
    return hashlib.sha256(canon.encode()).hexdigest()


def _build_trade_setup(direction: Direction, last_close: float, atr: float) -> TradeSetupOut:
    if direction is Direction.NEUTRAL or atr <= 0:
        return TradeSetupOut(direction=direction.value)
    if direction is Direction.LONG:
        sl = last_close - 1.5 * atr
        tp = last_close + 3.0 * atr
    else:
        sl = last_close + 1.5 * atr
        tp = last_close - 3.0 * atr
    risk = abs(last_close - sl)
    reward = abs(tp - last_close)
    rr = reward / risk if risk > 0 else 0.0
    return TradeSetupOut(
        direction=direction.value, entry=round(last_close, 2),
        stop_loss=round(sl, 2), take_profit=round(tp, 2),
        risk_reward=round(rr, 2),
    )


def _atr(bars: pd.DataFrame, period: int = 14) -> float:
    if len(bars) < period + 1:
        return 0.0
    h = bars["high"].to_numpy(dtype=float)
    lo = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_close), np.abs(lo - prev_close)))
    return float(np.mean(tr[-period:]))


def _btc_atr_pct(bars: pd.DataFrame) -> float | None:
    """ATR-as-percent-of-close for the latest bar; ``None`` if not enough bars."""
    if len(bars) < 15:
        return None
    last_close = float(bars["close"].iloc[-1])
    if last_close <= 0:
        return None
    return _atr(bars) / last_close


def _is_friday_close(bars: pd.DataFrame) -> bool:
    """True when the latest bar's timestamp is a Friday in UTC.

    The trap module ``friday_weekend`` enforces a stricter "near-close" check
    based on the timeframe; we surface only the broad weekday signal here so
    the trap detector keeps full control of the firing window.
    """
    try:
        ts = bars.index[-1]
        return bool(getattr(ts, "weekday", lambda: None)() == 4)
    except Exception:  # noqa: BLE001
        return False


def _weekly_bias(bars: pd.DataFrame) -> Direction:
    """Direction of the last 7 daily bars-equivalent (uses last 168 1h-equivalents).

    Falls back to ``NEUTRAL`` when there are not enough bars or when the move
    is < 1 % (tight band — we want a real bias, not noise).
    """
    if len(bars) < 168:
        return Direction.NEUTRAL
    try:
        first = float(bars["close"].iloc[-168])
        last = float(bars["close"].iloc[-1])
        if first <= 0:
            return Direction.NEUTRAL
        delta_pct = (last - first) / first
        if delta_pct > 0.01:
            return Direction.LONG
        if delta_pct < -0.01:
            return Direction.SHORT
    except Exception:  # noqa: BLE001
        return Direction.NEUTRAL
    return Direction.NEUTRAL


def _build_extras(
    *,
    static_score: float,
    brain_adjust: float,
    trap_count: int,
    news_multiplier: float,
    final_score: float,
    final_direction: Direction,
    fires: list[Any],
    tier: str,
) -> dict[str, Any]:
    """Assemble the JSONB-bound extras payload for ``predictions.layer_scores``.

    Stored separately from the typed ``layer_scores`` map so we don't widen
    the API ``LayerScoreOut`` schema. Persistence sites merge ``extras``
    on top of the per-layer scores when serialising to JSONB.
    """
    effective_count = min(trap_count, _TRAP_CAP)
    trap_factor = (1.0 - _TRAP_PENALTY) ** effective_count
    direction_penalty = (
        _SHORT_DIRECTION_PENALTY if final_direction is Direction.SHORT else 1.0
    )
    return {
        "traps_fired": [
            {
                "trap_id": f.trap_id,
                "severity": f.severity,
                "side": f.side,
                "reason": f.reason,
                "evidence": dict(f.evidence) if f.evidence else {},
            }
            for f in fires
        ],
        "static_score": static_score,
        "brain_adjust": brain_adjust,
        "trap_factor": trap_factor,
        "news_multiplier": news_multiplier,
        "direction_penalty": direction_penalty,
        "final": final_score,
        "tier": tier,
    }


def build_prediction(
    *,
    symbol: str,
    timeframe: str,
    bars: pd.DataFrame,
    pattern_stats_lookup: PatternStatsLookup | None = None,
    enabled_patterns: set[str] | None = None,
    enabled_traps: set[str] | None = None,
    ghost: GhostInput | None = None,
) -> LivePredictionOut:
    """Score all 10 layers, run the 17-trap stack, and tag the resulting tier.

    SP-5 Phase F1 wiring: extends the SP-2 ``L1+L2+L3+L5`` shape to the full
    ten-slot grid (L4 SMC, L6 micro, L7/L9/L10 placeholders, L8 hookup). The
    aggregator is invoked twice — once with no traps to derive the proposed
    direction, once again after :func:`check_all_traps` produces the trap
    fires — so the trap orchestrator sees the candidate side. The enriched
    payload (raw scores, trap multiplier, direction penalty, final, tier) is
    stashed on ``LivePredictionOut.prediction_extras`` for the
    ``predictions.layer_scores`` JSONB persistence sites to merge into the
    per-layer dict.
    """
    layer_results: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    layer_results[1] = score_l1(bars)
    if pattern_stats_lookup is not None and len(bars) > 0:
        layer_results[2] = score_l2(
            bars,
            current_idx=len(bars) - 1,
            stats=pattern_stats_lookup,
            enabled_patterns=enabled_patterns,
        )
    layer_results[3] = score_l3(bars)
    layer_results[4] = score_l4(bars)
    layer_results[5] = score_l5(bars)
    layer_results[6] = score_l6(bars)
    layer_results[7] = score_l7(bars)
    layer_results[8] = score_l8(bars, ghost=ghost)
    # L9 (news + sentiment, SP-9 Phase E1) is async and needs a DB session;
    # the wiring lands in Phase E2. Until then, L9 stays None (placeholder
    # behaviour preserved — aggregator redistributes weight per SP-5 §3.4).
    layer_results[9] = None
    layer_results[10] = score_l10(bars)

    # First aggregator pass — no traps — to derive the proposed direction the
    # trap stack will see. brain_adjust / news_multiplier default to 1.0.
    proposed = aggregate(layer_results)
    static_score = proposed.score
    proposed_direction = proposed.direction

    # TrapContext: live data sources (funding, OI delta, borrow rate, news
    # event time) are still TODO (SP-3.5 / SP-9). Compute the cheap bar-derived
    # fields locally so the per-context traps that need them stop being blind.
    context = TrapContext(
        next_news_event_minutes_until=None,
        is_friday_close=_is_friday_close(bars),
        weekly_bias=_weekly_bias(bars),
        btc_atr_pct=_btc_atr_pct(bars),
        funding_rate=None,
        open_interest_delta_24h=None,
        borrow_rate_pct=None,
        symbol=symbol,
        timeframe=timeframe,
    )

    fires = check_all_traps(
        bars=bars,
        current_idx=len(bars) - 1,
        layer_scores=layer_results,
        proposed_direction=proposed_direction,
        context=context,
        enabled_set=enabled_traps,
    )

    # Second pass — apply traps (and the neutral brain / news multipliers).
    final = aggregate(
        layer_results,
        trap_fires=fires,
        brain_adjust=1.0,
        news_multiplier=1.0,
    )
    tier = classify_tier(final)

    closes = bars["close"].to_numpy(dtype=float)
    rsi14 = rsi(closes, 14)
    macd_line, macd_signal, macd_hist = macd(closes, 12, 26, 9)

    def _safe(arr: np.ndarray) -> float | None:
        v = float(arr[-1])
        return None if math.isnan(v) else v

    momentum = MomentumPanelOut(
        rsi=_safe(rsi14),
        macd_line=_safe(macd_line),
        macd_signal=_safe(macd_signal),
        macd_hist=_safe(macd_hist),
    )

    trade_setup = _build_trade_setup(final.direction, float(closes[-1]), _atr(bars))
    extras = _build_extras(
        static_score=static_score,
        brain_adjust=1.0,
        trap_count=len(fires),
        news_multiplier=1.0,
        final_score=final.score,
        final_direction=final.direction,
        fires=fires,
        tier=tier,
    )

    return LivePredictionOut(
        symbol=symbol,
        timeframe=timeframe,
        ts=bars.index[-1].to_pydatetime(),
        price=float(closes[-1]),
        final=FinalScoreOut(
            score=final.score, direction=final.direction.value,
            confidence=final.confidence,
            contributing_layers=list(final.contributing_layers),
        ),
        layer_scores={str(i): _layer_to_out(s) for i, s in layer_results.items()},
        trade_setup=trade_setup,
        momentum=momentum,
        cold_start=True,
        inputs_hash=_compute_inputs_hash(symbol, timeframe, bars),
        prediction_extras=extras,
    )
