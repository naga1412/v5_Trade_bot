import asyncio
import hashlib
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Literal

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    FinalScoreOut,
    LayerScoreOut,
    LivePredictionOut,
    MomentumPanelOut,
    NewsSummary,
    SentimentSummary,
    TradeSetupOut,
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
from app.core.scoring.layer9_news import score as score_l9
from app.core.scoring.layer10_brain import score as score_l10
from app.rl.obs import MacroFeatures, MarketFeatures, PositionState
from app.rl.predictor_glue import compute_brain_adjust_and_persist
from app.core.scoring.run_traps import check_all_traps
from app.core.scoring.tiers import classify_tier
from app.core.scoring.traps.base import TrapContext
from app.core.scoring.types import Direction, LayerScore

log = logging.getLogger(__name__)

_TRAP_PENALTY: float = 0.15
_TRAP_CAP: int = 4
# Kept in sync with app/core/scoring/aggregator.py. 1.0 = no penalty
# (symmetric LONG/SHORT, 2026-05-14).
_SHORT_DIRECTION_PENALTY: float = 1.0
# PR-PLUMBING-1 Fix 1: Binance funding events per day (every 8h = 3/day).
# Used to convert the per-8h `lookup_latest_funding_rate` return value to the
# per-day fraction `evaluate_funding_rate` expects.
_FUNDING_EVENTS_PER_DAY: int = 3


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


_NEWS_SUMMARY_LOOKBACK_MIN: int = 60
_IMPACT_HIGH_THRESHOLD: float = 0.7
_IMPACT_MEDIUM_THRESHOLD: float = 0.4


def _bias_from_layer(layer: LayerScore | None) -> Literal["Bullish", "Bearish", "Neutral"]:
    """Map an L9 LayerScore.direction to the human bias label."""
    if layer is None:
        return "Neutral"
    if layer.direction is Direction.LONG:
        return "Bullish"
    if layer.direction is Direction.SHORT:
        return "Bearish"
    return "Neutral"


async def _build_news_summary(
    *,
    symbol: str,
    session: AsyncSession,
    now: datetime | None = None,
) -> NewsSummary | None:
    """Aggregate the last-60min news_items for ``symbol`` into a summary.

    Returns ``None`` when there are no rows or any DB error occurs — the
    Tab 1 panel falls back to its placeholder UI in that case.
    """
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(
        minutes=_NEWS_SUMMARY_LOOKBACK_MIN,
    )
    base = symbol.split("/")[0].upper()

    dialect = session.bind.dialect.name if session.bind else "postgresql"
    is_pg = dialect.startswith("postgres")

    try:
        if is_pg:
            sql = sa.text(
                "SELECT title, impact_score FROM news_items "
                "WHERE published_at >= :cutoff "
                "AND :base = ANY(affected_assets)"
            )
            params: dict[str, object] = {"cutoff": cutoff, "base": base}
        else:
            sql = sa.text(
                "SELECT title, impact_score FROM news_items "
                "WHERE published_at >= :cutoff "
                "AND affected_assets LIKE :pat"
            )
            params = {"cutoff": cutoff, "pat": f'%"{base}"%'}
        rows = (await session.execute(sql, params)).all()
    except Exception:  # noqa: BLE001 — never let news lookup break scoring
        log.warning("news summary query failed; skipping", exc_info=True)
        return None

    if not rows:
        return None

    impacts = [
        float(r.impact_score) for r in rows if r.impact_score is not None
    ]
    avg_impact = (sum(impacts) / len(impacts)) if impacts else 0.0
    if avg_impact > _IMPACT_HIGH_THRESHOLD:
        impact: Literal["LOW", "MEDIUM", "HIGH"] = "HIGH"
    elif avg_impact > _IMPACT_MEDIUM_THRESHOLD:
        impact = "MEDIUM"
    else:
        impact = "LOW"

    # Top headline = the row with the highest impact_score (None treated as 0).
    top_row = max(
        rows, key=lambda r: float(r.impact_score) if r.impact_score is not None else 0.0,
    )
    top_headline = str(top_row.title) if top_row.title is not None else None

    return NewsSummary(
        recent_count=len(rows),
        top_headline=top_headline,
        impact=impact,
    )


async def _build_sentiment_summary(
    *,
    l9: LayerScore | None,
) -> SentimentSummary | None:
    """Fetch the F&G index + map L9 direction to a news_bias label.

    Returns ``None`` when the F&G call fails or the index is unavailable —
    the Tab 1 panel falls back to its placeholder UI in that case.
    """
    # Imported lazily so the schema layer doesn't pull in ``httpx`` at import.
    from app.news.fear_greed import get_fear_greed_index

    try:
        fng = await get_fear_greed_index()
    except Exception:  # noqa: BLE001
        log.warning("F&G index fetch failed; skipping sentiment summary", exc_info=True)
        return None

    label = fng.label
    valid_labels = {"Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"}
    if label not in valid_labels:
        log.warning("F&G returned unknown label %r; skipping summary", label)
        return None

    return SentimentSummary(
        fng_value=int(fng.value),
        fng_label=label,  # type: ignore[arg-type]
        news_bias=_bias_from_layer(l9),
    )


async def _none_coro() -> None:
    """Returns None — used as a placeholder coroutine in asyncio.gather when
    a real async call can't be made (e.g., session is None)."""
    return None


async def _compute_aggregator_hook_fields(
    *,
    symbol: str,
    timeframe: str,
    bars: pd.DataFrame,
    final: Any,
    session: AsyncSession | None,
) -> tuple[
    int | None,    # mtf_agreement
    str | None,    # mtf_dominant_tf
    str | None,    # mtf_directions_json
    float | None,  # p_win
    float | None,  # effective_score
    float | None,  # realized_vol_20d
    float | None,  # funding_directional_adj
    float | None,  # funding_rate (per-8h, raw from Binance) — PR-PLUMBING-1
    str | None,    # mtf_adx_by_tf_json — PR-BOT-INTELLIGENCE-UPGRADE C3
]:
    """Compute the 7 PR1 record-only analytics fields.

    Record-only: NEVER modifies final_score, confidence, direction,
    layer_scores. Fail-open on every helper.

    Three async helpers are fired in parallel via asyncio.gather, then
    two sync helpers compute on their results. Parallelism matters for
    the V-7 latency gate (Phase 7).
    """
    from app.core.scoring.mtf_confluence import compute_mtf_confluence
    from app.core.scoring.p_win_calibrator import predict_p_win
    from app.core.scoring.intermarket_lookup import lookup_latest_funding_rate
    from app.core.scoring.vol_normalization import (
        compute_effective_score,
        compute_realized_vol_20d,
    )
    from app.core.scoring.funding_directional import compute_funding_directional_adj

    # Adapter: pd.DataFrame (ts is DatetimeIndex) → list[Bar-like] objects
    # with .ts + .close attributes. compute_realized_vol_20d expects this
    # shape per the operator-locked contract.
    _bar_list = [
        SimpleNamespace(
            ts=_ts.to_pydatetime() if hasattr(_ts, "to_pydatetime") else _ts,
            close=float(_close),
        )
        for _ts, _close in zip(bars.index, bars["close"])
    ]

    # Synthesize three async tasks. Each helper individually fails-open
    # (returns None on internal error), but we wrap the whole gather in
    # try/except as defense against unexpected exceptions.
    _binance_symbol_for_mtf = symbol.replace("/", "")  # BTC/USDT → BTCUSDT
    mtf = None
    p_win_val = None
    funding_rate = None
    try:
        mtf, p_win_val, funding_rate = await asyncio.gather(
            compute_mtf_confluence(_binance_symbol_for_mtf, final.direction),
            predict_p_win(final.score, final.direction),
            (
                lookup_latest_funding_rate(session, _binance_symbol_for_mtf)
                if session is not None
                else _none_coro()
            ),
        )
    except Exception as exc:  # noqa: BLE001 — recording-only fail-open
        log.exception("aggregator_hook: unexpected async failure: %s", exc)
        # mtf, p_win_val, funding_rate stay None — see initialization above

    # Sync helpers (CPU-only, can't raise meaningfully if inputs are sane,
    # but wrap to preserve fail-open contract).
    realized_vol = None
    effective = None
    funding_adj = None
    try:
        realized_vol = compute_realized_vol_20d(_bar_list)
        effective = compute_effective_score(final.score, realized_vol)
        funding_adj = compute_funding_directional_adj(funding_rate, final.direction)
    except Exception as exc:  # noqa: BLE001 — fail-open
        log.exception("aggregator_hook: sync compute failed: %s", exc)

    # Pack MTF result into the 3 Pydantic fields.
    mtf_agreement_val: int | None = None
    mtf_dominant_tf_val: str | None = None
    mtf_directions_json_val: str | None = None
    mtf_adx_by_tf_json_val: str | None = None
    if mtf is not None:
        mtf_agreement_val = mtf.agreement
        mtf_dominant_tf_val = mtf.dominant_tf
        try:
            # Canonical form: sort_keys=True + tight separators. MUST match
            # `_normalize_mtf_directions_json` in app/db/payload_builders.py
            # so the bytes are stable on round-trip through asyncpg's JSONB
            # decode (PR-MTF-DIRECTIONS-JSON-SERIALIZATION-FIX, 2026-05-23).
            # Without this, a position re-loaded from `shadow_open_positions`
            # after a container restart re-serializes to a different byte
            # sequence (default `", "` + `": "` separators, unsorted keys),
            # which is pure hygiene noise — the column is in
            # NON_HASHED_ALLOW_LIST so chain hashes are unaffected.
            mtf_directions_json_val = json.dumps(
                mtf.directions, sort_keys=True, separators=(",", ":"),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("aggregator_hook: mtf.directions json.dumps failed: %s", exc)
        # PR-BOT-INTELLIGENCE-UPGRADE C3: serialize per-TF ADX alongside
        # directions. In-memory only (no DB column); the dispatcher's
        # open_position_gate parses it back when ADX_GATE_ENABLED.
        # `getattr` default keeps test stubs that pre-date the C3 field
        # (e.g. SimpleNamespace fakes) silent — they'll get an empty {}
        # which serializes cleanly, no warning noise.
        _adx_by_tf = getattr(mtf, "adx_by_tf", None) or {}
        try:
            mtf_adx_by_tf_json_val = json.dumps(
                _adx_by_tf, sort_keys=True, separators=(",", ":"),
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("aggregator_hook: mtf.adx_by_tf json.dumps failed: %s", exc)

    log.info(
        "aggregator_hook %s/%s: mtf_attached=%s p_win=%s effective_score=%s funding_adj=%s",
        symbol, timeframe,
        mtf is not None,
        p_win_val if p_win_val is not None else "none",
        effective if effective is not None else "none",
        funding_adj if funding_adj is not None else "none",
    )

    return (
        mtf_agreement_val,
        mtf_dominant_tf_val,
        mtf_directions_json_val,
        p_win_val,
        effective,
        realized_vol,
        funding_adj,
        # PR-PLUMBING-1 Fix 1: surface the raw per-8h funding rate to the
        # caller so build_prediction can convert (×3) and attach to
        # LivePredictionOut.funding_rate_daily for the dispatcher's
        # funding-rate kill-switch.
        funding_rate,
        # PR-BOT-INTELLIGENCE-UPGRADE C3: serialized per-TF ADX JSON.
        mtf_adx_by_tf_json_val,
    )


async def build_prediction(
    *,
    symbol: str,
    timeframe: str,
    bars: pd.DataFrame,
    pattern_stats_lookup: PatternStatsLookup | None = None,
    enabled_patterns: set[str] | None = None,
    enabled_traps: set[str] | None = None,
    ghost: GhostInput | None = None,
    session: AsyncSession | None = None,
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
    # L9 (news + sentiment, SP-9 Phase E2): async + needs a DB session.
    # When ``session`` is None, L9 abstains (None) so legacy callers that
    # don't pass a session keep the SP-5 placeholder behaviour.
    if session is not None:
        try:
            layer_results[9] = await score_l9(symbol=symbol, session=session)
        except Exception:  # noqa: BLE001 — never let news lookup break scoring
            log.warning("L9 news layer query failed; abstaining", exc_info=True)
            layer_results[9] = None
    else:
        layer_results[9] = None
    layer_results[10] = score_l10(bars)

    # First aggregator pass — no traps — to derive the proposed direction the
    # trap stack will see. brain_adjust / news_multiplier default to 1.0.
    proposed = aggregate(layer_results)
    static_score = proposed.score
    proposed_direction = proposed.direction

    context = await _build_trap_context(
        symbol=symbol, timeframe=timeframe, bars=bars, session=session,
    )

    fires = check_all_traps(
        bars=bars,
        current_idx=len(bars) - 1,
        layer_scores=layer_results,
        proposed_direction=proposed_direction,
        context=context,
        enabled_set=enabled_traps,
    )

    # SP-4 Phase C — brain hook. Returns brain_adjust=1.0 (no-op) when no
    # checkpoint is loaded, so the pre-SP-4 equal-weight behaviour is
    # bit-identical until the first PPO policy is activated.
    def _layer_signed(i: int) -> float:
        ls = layer_results[i]
        return ls.signed_strength if ls is not None else 0.0
    brain_layers = tuple(_layer_signed(i) for i in range(1, 10))
    brain_market = MarketFeatures(
        atr_pct=context.btc_atr_pct or 0.0,
        funding_rate=context.funding_rate or 0.0,
        oi_delta_24h=context.open_interest_delta_24h or 0.0,
        dxy_corr_30d=0.0,
        gold_corr_30d=0.0,
        regime="sideways_grind",  # SP-4 Phase D wires real regime detection
    )
    brain_position = PositionState(
        cur_position=0, unrealized_pnl_R=0.0, bars_in_position=0,
    )
    brain_macro = MacroFeatures(
        hours_to_next_high_impact=float(
            context.next_news_event_minutes_until or 60 * 24
        ) / 60.0,
        fomc_window=False, weekend=False, asia_open=False,
    )
    brain_hook = await compute_brain_adjust_and_persist(
        symbol=symbol,
        proposed_direction=proposed_direction.value,
        layer_scores=brain_layers,
        market=brain_market,
        position=brain_position,
        macro=brain_macro,
        session=session,
    )

    # Second pass — apply traps + brain multiplier + news multiplier.
    final = aggregate(
        layer_results,
        trap_fires=fires,
        brain_adjust=brain_hook.brain_adjust,
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

    # SP-9 Phase F1: build the optional Tab 1 sentiment + news summaries.
    # Both are best-effort — any failure leaves the field as None so legacy
    # callers (no session, no news_items table, no F&G upstream) keep
    # working unchanged.
    news_summary: NewsSummary | None = None
    sentiment_summary: SentimentSummary | None = None
    if session is not None:
        try:
            news_summary = await _build_news_summary(symbol=symbol, session=session)
        except Exception:  # noqa: BLE001
            log.warning("news summary build failed; skipping", exc_info=True)
            news_summary = None
        try:
            sentiment_summary = await _build_sentiment_summary(l9=layer_results[9])
        except Exception:  # noqa: BLE001
            log.warning("sentiment summary build failed; skipping", exc_info=True)
            sentiment_summary = None

    # ─── PR1 Task 3.4: aggregator hook ───────────────────────────────────
    # Record-only attach of 7 analytics fields. NEVER modifies final_score,
    # confidence, direction, layer_scores. Fail-open on every helper.
    # PR-PLUMBING-1 Fix 1: hook now also surfaces the raw per-8h funding
    # rate so build_prediction can attach a per-day value to the schema
    # for the dispatcher's funding-rate kill-switch.
    (
        _mtf_agreement,
        _mtf_dominant_tf,
        _mtf_directions_json,
        _p_win,
        _effective_score,
        _realized_vol_20d,
        _funding_directional_adj,
        _funding_rate_per_8h,
        _mtf_adx_by_tf_json,
    ) = await _compute_aggregator_hook_fields(
        symbol=symbol,
        timeframe=timeframe,
        bars=bars,
        final=final,
        session=session,
    )
    # Convert per-8h Binance funding rate → per-day fraction. None-guard so a
    # missing lookup (no session, no rows, fail-open) propagates as None rather
    # than crashing on arithmetic.
    _funding_rate_daily = (
        _funding_rate_per_8h * _FUNDING_EVENTS_PER_DAY
        if _funding_rate_per_8h is not None else None
    )
    # ─── end aggregator hook ─────────────────────────────────────────────

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
        sentiment=sentiment_summary,
        news=news_summary,
        # PR1 Task 3.4 record-only attachments:
        mtf_agreement=_mtf_agreement,
        mtf_dominant_tf=_mtf_dominant_tf,
        mtf_directions_json=_mtf_directions_json,
        p_win=_p_win,
        effective_score=_effective_score,
        realized_vol_20d=_realized_vol_20d,
        funding_directional_adj=_funding_directional_adj,
        # PR-PLUMBING-1 Fix 1: raw daily funding rate for the dispatcher
        # kill-switch (None = lookup failed / no session — gate falls open).
        funding_rate_daily=_funding_rate_daily,
        # PR-BOT-INTELLIGENCE-UPGRADE C3: serialized per-TF ADX dict (in-memory
        # only — the entry-quality gate reads dominant_tf's ADX for its global
        # trend-strength check). None when MTF compute itself returned None.
        mtf_adx_by_tf_json=_mtf_adx_by_tf_json,
    )


async def _intermarket_snapshot_for(
    symbol: str, session: AsyncSession,
) -> tuple[float | None, float | None]:
    """Return ``(funding_rate, oi_delta_24h_pct)`` from intermarket_snapshots.

    Looks up:
      * the latest row for ``symbol`` → funding_rate,
      * the latest row at-or-before ``latest.captured_at - 24h`` → baseline OI,
      * delta = (latest.OI - baseline.OI) / baseline.OI.

    Any failure path (no rows, no baseline, baseline OI <= 0, DB error)
    returns ``(funding, None)`` or ``(None, None)`` — never raises.
    """
    from app.data.intermarket_persistence import (
        latest_snapshot_for, snapshot_at_or_before,
    )
    try:
        latest = await latest_snapshot_for(session, symbol)
        if latest is None:
            return (None, None)
        funding = latest.funding_rate
        if latest.open_interest is None:
            return (funding, None)
        baseline = await snapshot_at_or_before(
            session, symbol,
            ts=latest.captured_at - timedelta(hours=24),
        )
        if baseline is None or baseline.open_interest is None or baseline.open_interest <= 0:
            return (funding, None)
        oi_delta = (latest.open_interest - baseline.open_interest) / baseline.open_interest
        return (funding, oi_delta)
    except Exception:  # noqa: BLE001
        log.warning("_intermarket_snapshot_for(%s) failed", symbol, exc_info=True)
        return (None, None)


async def _build_trap_context(
    *,
    symbol: str,
    timeframe: str,
    bars: pd.DataFrame,
    session: AsyncSession | None,
) -> TrapContext:
    """Compose the SP-5 TrapContext, wiring SP-3.5 funding + OI when available.

    When ``session is None`` (legacy callers with no DB), funding +
    open_interest_delta_24h are left None; the trap stack abstains those
    two short traps for that bar — same behavior as before SP-3.5.

    ``borrow_rate_pct`` is permanently None (spec §6 row 2).
    ``next_news_event_minutes_until`` is None until the news-calendar
    follow-up wires it (SP-9.5).
    """
    funding, oi_delta = (None, None)
    if session is not None:
        funding, oi_delta = await _intermarket_snapshot_for(symbol, session)
    return TrapContext(
        next_news_event_minutes_until=None,
        is_friday_close=_is_friday_close(bars),
        weekly_bias=_weekly_bias(bars),
        btc_atr_pct=_btc_atr_pct(bars),
        funding_rate=funding,
        open_interest_delta_24h=oi_delta,
        borrow_rate_pct=None,
        symbol=symbol,
        timeframe=timeframe,
    )
