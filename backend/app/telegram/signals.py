"""SP-8 Phase G — per-trade Telegram approval messages.

Spec §7.2: each candidate trade fires a Telegram DM to the user
with the entry/SL/TP/leverage breakdown + inline buttons:

    [ ✅ Approve N× ]   [ +1× ]   [ -1× ]
    [ ⚙ Custom leverage ]   [ ❌ Skip ]

Tapping +1× / -1× re-renders the same message with the leverage
incremented and the dollar-loss / liquidation-buffer math updated.
Custom leverage triggers a reply-prompt. Skip closes the signal.
Auto-skip on §7.4 timeout (default 30s).

This module is pure — it builds message bodies, button keyboards, and
state transitions. The polling worker that posts the message + listens
for callbacks lives in Phase J wiring (extends the SP-4 Phase E
telegram_bot.py).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from app.trading.leverage import (
    liquidation_distance_pct,
    loss_at_stop_loss_usdt,
    recommended_leverage,
)


_DEFAULT_AUTO_SKIP_SECONDS = 30
_DEFAULT_HARD_CAP = 10
_MIN_LEVERAGE = 1


@dataclass(frozen=True)
class SignalCandidate:
    """The raw signal data the message renders from."""

    signal_id: str
    symbol: str
    timeframe: str
    direction: Literal["LONG", "SHORT"]
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    confidence_pct: float            # 0-100
    # {"1": {"direction": "LONG", "strength": 0.85, "confidence": 0.7,
    #         "notes": "..."}, "9": None, ...} — None values are tolerated
    # (abstained layers render as "-- (abstained)"). The synthetic
    # {"score": float, "note": str} shape used by admin_test_trade.py is
    # still accepted by the renderer.
    layer_summary: dict[str, dict | None]
    margin_usdt: float
    funding_rate_daily: float        # signed; +0.012 means longs pay shorts 1.2%/day
    chart_url: str
    sl_distance_pct: float           # 0.02 = 2%; precomputed by caller
    rr_ratio: float                  # take_profit/stop_loss distance ratio
    # PR2: MTF state at the moment the signal cleared the gate. Persisted
    # in telegram_signals.payload so the approve-time path can populate
    # live_trades.mtf_* (Phase 7) without re-deriving from the prediction
    # row. None for legacy callers / pre-PR2 fixtures.
    mtf_agreement: int | None = None
    mtf_dominant_tf: str | None = None
    mtf_directions: dict[str, int] | None = None


@dataclass(frozen=True)
class RenderedMessage:
    """A Telegram-ready message: body text + inline keyboard."""

    body: str
    inline_keyboard: list[list[dict]]


def _layer_signed_score(data: dict) -> float | None:
    """Read a signed score from a layer dict in either shape.

    Two shapes coexist in callers of render_message:

      * ``{"score": float, "note": str}`` — synthetic shape used by
        ``admin_test_trade.py`` and the original unit-test fixture.
      * ``{"direction": "LONG"/"SHORT"/"NEUTRAL", "strength": float,
        "confidence": float, "notes": str}`` — what
        ``LayerScoreOut.model_dump()`` produces in production
        (via ``live_prediction._layer_payload``).

    The function returns the signed score for either shape. None when
    neither shape matches (e.g. a synthetic dict missing both ``score``
    and ``strength``).
    """
    if "score" in data:
        score = data["score"]
        return float(score) if score is not None else None
    strength = data.get("strength")
    if strength is None:
        return None
    direction = data.get("direction")
    if direction == "LONG":
        return float(strength)
    if direction == "SHORT":
        return -float(strength)
    return 0.0  # NEUTRAL


def _format_layers(layers: dict[str, Any]) -> str:
    """Render the per-layer breakdown for the Telegram message body.

    Tolerant of three failure modes uncovered 2026-05-25 / 2026-05-26:
      1. An entry can be ``None`` when its upstream layer abstained
         (e.g. L9 news returns None when no items match the symbol;
         L2 patterns stays None if pattern_stats_lookup load failed).
         These render as ``-- (abstained)`` rather than raising
         ``AttributeError: 'NoneType' object has no attribute 'get'``,
         which used to escape into the dispatcher's outer except and
         convert the entire dispatch to outcome=error — silently
         dropping the strongest signal that cleared every gate.
      2. The original implementation read ``data["score"]`` but
         ``LayerScoreOut.model_dump()`` emits ``{direction, strength,
         confidence, notes}`` with no ``"score"`` key, so production
         messages had been rendering ``L1: --`` for every layer since
         SP-9 wired L9. Now reads via ``_layer_signed_score`` which
         accepts both shapes.
      3. An entry can be a non-dict scalar / list / string when the
         upstream caller merged ``prediction_extras`` into the layer
         payload (live_prediction.py:208 does
         ``_layer_payload.update(pred.prediction_extras)``). The
         merged keys — ``static_score``, ``brain_adjust``,
         ``trap_factor``, ``news_multiplier``, ``direction_penalty``,
         ``final``, ``tier``, ``traps_fired`` — are metadata, not
         layers, so we skip them silently. The previous behavior was
         ``"score" in <float>`` → ``TypeError: argument of type
         'float' is not iterable``, which escaped to the dispatcher's
         outer except and converted dispatch to outcome=error —
         dropping every signal whose ``prediction_extras`` were
         attached (i.e. every signal that reached the render path
         post-SP-5 Phase F1).
    """
    if not layers:
        return "  (no layer scores)"
    lines = []
    for name, data in sorted(layers.items()):
        if data is None:
            lines.append(f"  {name}:    --  (abstained)")
            continue
        # PR-FIX-DISPATCH-FLOAT-NOT-ITERABLE: skip metadata merged from
        # prediction_extras (floats / strings / lists). See docstring §3.
        if not isinstance(data, dict):
            continue
        score = _layer_signed_score(data)
        note = data.get("note") or data.get("notes", "") or ""
        sign = "+" if score is not None and score >= 0 else ""
        score_str = f"{sign}{score:.2f}" if score is not None else "  --"
        direction_marker = (
            "LONG" if score is not None and score > 0
            else "SHORT" if score is not None and score < 0
            else "—"
        )
        lines.append(
            f"  {name}:  {score_str} ({direction_marker}, {note})"
        )
    return "\n".join(lines)


def _format_funding_text(funding_rate_daily: float, direction: str) -> str:
    """Spec §7.2: funding text mentions whether you pay or receive."""
    pct = funding_rate_daily * 100
    if direction == "LONG":
        if funding_rate_daily > 0:
            return f"{pct:+.3f}% (you pay funding)"
        elif funding_rate_daily < 0:
            return f"{pct:+.3f}% (you receive funding)"
    else:  # SHORT
        if funding_rate_daily > 0:
            return f"{pct:+.3f}% (you receive funding)"
        elif funding_rate_daily < 0:
            return f"{pct:+.3f}% (you pay funding)"
    return f"{pct:+.3f}%"


def render_message(
    candidate: SignalCandidate,
    *,
    leverage: int,
    auto_skip_seconds: int = _DEFAULT_AUTO_SKIP_SECONDS,
    now: datetime | None = None,
) -> RenderedMessage:
    """Build the Telegram message body + inline keyboard for spec §7.2."""
    n = now or datetime.now(timezone.utc)
    direction_emoji = "🔔"
    sl_pct = candidate.sl_distance_pct * 100
    tp_distance = abs(
        candidate.take_profit_price - candidate.entry_price
    ) / candidate.entry_price
    tp_pct = tp_distance * 100

    position_value = candidate.margin_usdt * leverage
    loss_at_sl = loss_at_stop_loss_usdt(
        candidate.margin_usdt, leverage, candidate.sl_distance_pct,
    )
    pct_of_margin = (
        (loss_at_sl / candidate.margin_usdt) * 100
        if candidate.margin_usdt > 0 else 0.0
    )

    liq_distance = liquidation_distance_pct(leverage)
    if candidate.direction == "LONG":
        liq_price = candidate.entry_price * (1 - liq_distance)
    else:
        liq_price = candidate.entry_price * (1 + liq_distance)

    safety_buffer_x = (
        liq_distance / candidate.sl_distance_pct
        if candidate.sl_distance_pct > 0 else 0.0
    )

    max_safe = recommended_leverage(
        margin_usdt=candidate.margin_usdt,
        sl_distance_pct=candidate.sl_distance_pct,
        hard_cap=125,
    )

    body = (
        f"{direction_emoji} {candidate.direction}  •  {candidate.symbol}  "
        f"•  {n.strftime('%d %b %Y %H:%M UTC')}\n"
        f"─────────────────────────────────────\n"
        f"Entry:        ${candidate.entry_price:,.2f}\n"
        f"Stop loss:    ${candidate.stop_loss_price:,.2f}  "
        f"({-sl_pct:+.1f}%)\n"
        f"Take profit:  ${candidate.take_profit_price:,.2f}  "
        f"({tp_pct:+.1f}%)\n"
        f"RR ratio:     {candidate.rr_ratio:.2f} : 1\n"
        f"Confidence:   {candidate.confidence_pct:.0f}%\n"
        f"\n"
        f"Layer scores:\n{_format_layers(candidate.layer_summary)}\n"
        f"─────────────────────────────────────\n"
        f"Margin:       ${candidate.margin_usdt:.2f} USDT\n"
        f"Leverage:     {leverage}× (math: max safe "
        f"{min(max_safe, _DEFAULT_HARD_CAP)}×, capped at "
        f"{_DEFAULT_HARD_CAP}× for risk profile)\n"
        f"Position:     ${position_value:,.2f}\n"
        f"Loss at SL:   ${loss_at_sl:.2f} ({pct_of_margin:.0f}% of margin)\n"
        f"Liquidation:  ${liq_price:,.2f}  "
        f"({-liq_distance*100:+.0f}% adverse)\n"
        f"Buffer:       {safety_buffer_x:.1f}× safety vs SL\n"
        f"Funding rate: "
        f"{_format_funding_text(candidate.funding_rate_daily, candidate.direction)}\n"
        f"─────────────────────────────────────\n"
        f"🔗 View on chart with ghost candle:\n"
        f"{candidate.chart_url}\n"
        f"─────────────────────────────────────\n"
        f"Auto-skip in {auto_skip_seconds}s if no response"
    )

    keyboard = _build_keyboard(candidate.signal_id, leverage)
    return RenderedMessage(body=body, inline_keyboard=keyboard)


def _build_keyboard(signal_id: str, leverage: int) -> list[list[dict]]:
    """Spec §7.2: 5-button inline keyboard layout.

    Row 1:  [ Approve N× ]  [ +1× ]  [ -1× ]
    Row 2:  [ Custom leverage ]  [ Skip ]
    """
    minus_lev = max(_MIN_LEVERAGE, leverage - 1)
    plus_lev = min(_DEFAULT_HARD_CAP, leverage + 1)

    return [
        [
            {
                "text": f"✅ Approve {leverage}×",
                "callback_data": f"sig:{signal_id}:approve:{leverage}",
            },
            {
                "text": "+1×",
                "callback_data": f"sig:{signal_id}:adjust:{plus_lev}",
            },
            {
                "text": "-1×",
                "callback_data": f"sig:{signal_id}:adjust:{minus_lev}",
            },
        ],
        [
            {
                "text": "⚙ Custom leverage",
                "callback_data": f"sig:{signal_id}:custom",
            },
            {
                "text": "❌ Skip",
                "callback_data": f"sig:{signal_id}:skip",
            },
        ],
    ]


# ---- Callback parsing -----------------------------------------------------


@dataclass(frozen=True)
class ParsedCallback:
    """A decoded Telegram callback_query.data payload."""

    signal_id: str
    action: Literal["approve", "adjust", "custom", "skip"]
    leverage: int | None  # set for approve + adjust


def parse_callback_data(data: str) -> ParsedCallback | None:
    """Decode a callback_data string. Returns None on malformed input.

    Format: ``sig:<signal_id>:<action>[:<lev>]``
    """
    if not data or not data.startswith("sig:"):
        return None
    parts = data.split(":")
    if len(parts) < 3 or len(parts) > 4:
        return None
    _, signal_id, action_raw = parts[:3]
    action = action_raw if action_raw in (
        "approve", "adjust", "custom", "skip",
    ) else None
    if action is None or not signal_id:
        return None
    leverage: int | None = None
    if len(parts) == 4:
        try:
            leverage = int(parts[3])
        except ValueError:
            return None
        if leverage < _MIN_LEVERAGE or leverage > _DEFAULT_HARD_CAP:
            return None
    if action in ("approve", "adjust") and leverage is None:
        return None
    return ParsedCallback(
        signal_id=signal_id, action=action, leverage=leverage,  # type: ignore[arg-type]
    )


def build_signal_payload(
    candidate: SignalCandidate, *,
    rendered_at: datetime,
    initial_leverage: int,
) -> dict:
    """Serialise a SignalCandidate for the telegram_signals.payload column.

    PR2: includes the 3 MTF state fields. The approve-time path reads
    them from this JSONB so live_trades.mtf_* gets populated when the
    user later approves the trade (matches the auto path's PR2 §4.4
    persistence contract). Pre-PR2 candidates carry None; the JSONB
    keys are still emitted so payload golden tests are stable.
    """
    return {
        "signal_id": candidate.signal_id,
        "symbol": candidate.symbol,
        "timeframe": candidate.timeframe,
        "direction": candidate.direction,
        "entry_price": candidate.entry_price,
        "stop_loss_price": candidate.stop_loss_price,
        "take_profit_price": candidate.take_profit_price,
        "confidence_pct": candidate.confidence_pct,
        "layer_summary": candidate.layer_summary,
        "margin_usdt": candidate.margin_usdt,
        "funding_rate_daily": candidate.funding_rate_daily,
        "chart_url": candidate.chart_url,
        "sl_distance_pct": candidate.sl_distance_pct,
        "rr_ratio": candidate.rr_ratio,
        "rendered_at": rendered_at.isoformat(),
        "initial_leverage": initial_leverage,
        "mtf_agreement": candidate.mtf_agreement,
        "mtf_dominant_tf": candidate.mtf_dominant_tf,
        "mtf_directions": candidate.mtf_directions,
    }


def serialise_payload(payload: dict) -> str:
    """JSON-encode for the JSONB column."""
    return json.dumps(payload, separators=(",", ":"))


__all__ = [
    "ParsedCallback",
    "RenderedMessage",
    "SignalCandidate",
    "build_signal_payload",
    "parse_callback_data",
    "render_message",
    "serialise_payload",
]
