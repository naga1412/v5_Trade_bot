import secrets
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# --- Spec thresholds (§5.1) ---
# Symmetric LONG/SHORT entry as of 2026-05-14: the original SHORT_THRESHOLD
# of -0.50 (vs LONG +0.30) reflected a CLAUDE.md / MASTER_PLAN policy that
# crypto squeezes can gap over short stops, so shorts needed stronger
# conviction. Combined with the model's empirical score distribution
# (p99=+0.353, p05=-0.074 over 95 historical predictions) it meant the
# SHORT gate was effectively unreachable — 0 / 95 historical scores cleared
# it, 0 / 8 paper trades were ever SHORT. Operator decision: trade both
# sides on equal terms so the validation ledger actually accumulates SHORT
# data. Pair with the matching changes in aggregator.py and tiers.py.
LONG_THRESHOLD: float = 0.30
SHORT_THRESHOLD: float = -0.30
MIN_CONFIDENCE: float = 0.50

# --- ATR multipliers (matches predictor.py) ---
SL_ATR_MULT: float = 1.5
TP_ATR_MULT: float = 3.0


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


def _gen_signal_id() -> str:
    """Short URL-safe id for chart deeplinks."""
    return secrets.token_urlsafe(6)


@dataclass(frozen=True)
class ShadowSignal:
    symbol: str
    direction: Direction
    score: float
    confidence: float
    entry_price: float
    stop_loss: float
    take_profit: float
    atr: float
    layer_scores: dict[str, Any]
    ts: datetime
    signal_id: str = field(default_factory=_gen_signal_id)

    @property
    def risk_reward(self) -> float:
        if self.direction is Direction.LONG:
            risk = self.entry_price - self.stop_loss
            reward = self.take_profit - self.entry_price
        else:
            risk = self.stop_loss - self.entry_price
            reward = self.entry_price - self.take_profit
        return reward / risk if risk > 0 else 0.0


@dataclass
class ShadowPosition:
    symbol: str
    direction: Direction
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size_usdt: float
    entry_score: float
    entry_confidence: float
    entry_atr: float
    layer_scores: dict[str, Any]
    bars_held: int
    opened_at: datetime
    last_check_at: datetime
    signal_id: str
    # PR3 Phase 2.2: per-position timeframe. Defaults to "1h" so PR1/PR2
    # callers that don't pass it get the legacy behavior bit-identically.
    # Persisted on `shadow_trades.timeframe` (PR1 added the column; PR3
    # makes it reflect actual entry TF rather than always '1h').
    timeframe: str = "1h"
    # PR3 Phase 5.5: G1 scaling fields. NULL when HOLD_TP_SCALING_ENABLED
    # is False (bit-identical to pre-G1). Populated by shadow_worker
    # open-trade hook when scaling is ON. Recording-only — out of
    # HASH_PAYLOAD_COLUMNS per policy (see app/db/audit.py).
    hold_scaling_factor: float | None = None
    hold_timeout_bars: int | None = None
    # PR-strategy-1 plumbing fix: PR1 analytics columns propagated from
    # `pred` at open time so persist_closed_trade can forward them into
    # `shadow_trades`. Default None; shadow_worker assigns directly
    # after `from_signal()` + before `persist_open_position()`. Each
    # column also lives in NON_HASHED_ALLOW_LIST per app/db/audit.py
    # (PR1 alembic migration 0020).
    #
    # KNOWN LIMITATION (per spec §3): `shadow_open_positions` does NOT
    # carry these columns. A restart between open + close → these are
    # None on the reloaded ShadowPosition → NULL on the closed trade.
    # Same-session open + close trades are populated correctly. Adding
    # the columns to `shadow_open_positions` (alembic + thread persist /
    # load) is the explicit follow-up — out of PR-strategy-1 scope.
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions_json: str | None = None
    p_win: float | None = None
    effective_score: float | None = None
    realized_vol_20d: float | None = None
    funding_directional_adj: float | None = None

    @classmethod
    def from_signal(cls, sig: ShadowSignal, *, position_size_usdt: float) -> "ShadowPosition":
        return cls(
            symbol=sig.symbol,
            direction=sig.direction,
            entry_price=sig.entry_price,
            stop_loss=sig.stop_loss,
            take_profit=sig.take_profit,
            position_size_usdt=position_size_usdt,
            entry_score=sig.score,
            entry_confidence=sig.confidence,
            entry_atr=sig.atr,
            layer_scores=sig.layer_scores,
            bars_held=0,
            opened_at=sig.ts,
            last_check_at=sig.ts,
            signal_id=sig.signal_id,
        )


@dataclass
class SignalEvaluator:
    """Pure decision logic. No DB or network. Test in isolation."""
    long_threshold: float = LONG_THRESHOLD
    short_threshold: float = SHORT_THRESHOLD
    min_confidence: float = MIN_CONFIDENCE
    sl_atr_mult: float = SL_ATR_MULT
    tp_atr_mult: float = TP_ATR_MULT

    def evaluate(
        self,
        *,
        symbol: str,
        score: float,
        confidence: float,
        last_close: float,
        atr: float,
        layer_scores: dict[str, Any],
        ts: datetime,
    ) -> ShadowSignal | None:
        """Returns a ShadowSignal if entry conditions met, else None."""
        if confidence < self.min_confidence:
            return None
        if atr <= 0:
            return None  # can't compute SL/TP

        if score > self.long_threshold:
            sl = last_close - self.sl_atr_mult * atr
            tp = last_close + self.tp_atr_mult * atr
            return ShadowSignal(
                symbol=symbol, direction=Direction.LONG,
                score=score, confidence=confidence,
                entry_price=last_close, stop_loss=sl, take_profit=tp,
                atr=atr, layer_scores=layer_scores, ts=ts,
            )
        if score < self.short_threshold:
            sl = last_close + self.sl_atr_mult * atr
            tp = last_close - self.tp_atr_mult * atr
            return ShadowSignal(
                symbol=symbol, direction=Direction.SHORT,
                score=score, confidence=confidence,
                entry_price=last_close, stop_loss=sl, take_profit=tp,
                atr=atr, layer_scores=layer_scores, ts=ts,
            )
        return None


@dataclass
class PositionGate:
    """Snapshot of open positions + cooldowns at decision time. Pure function."""
    open_symbols: set[str]
    cooldowns: dict[str, datetime]   # symbol -> cooldown_until

    def is_blocked(self, symbol: str, *, now: datetime) -> bool:
        if symbol in self.open_symbols:
            return True
        cd = self.cooldowns.get(symbol)
        if cd is not None and cd > now:
            return True
        return False
