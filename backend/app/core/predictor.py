import hashlib
import math
from datetime import datetime
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
from app.core.scoring.layer3_momentum import score as score_l3
from app.core.scoring.layer5_volume import score as score_l5
from app.core.scoring.types import Direction, LayerScore


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
    l = bars["low"].to_numpy(dtype=float)
    c = bars["close"].to_numpy(dtype=float)
    prev_close = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_close), np.abs(l - prev_close)))
    return float(np.mean(tr[-period:]))


def build_prediction(
    *, symbol: str, timeframe: str, bars: pd.DataFrame
) -> LivePredictionOut:
    layer_results: dict[int, LayerScore | None] = {i: None for i in range(1, 11)}
    layer_results[1] = score_l1(bars)
    layer_results[3] = score_l3(bars)
    layer_results[5] = score_l5(bars)

    final = aggregate(layer_results)

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
    )
